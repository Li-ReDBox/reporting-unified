import json
import logging
import requests
import concurrent.futures
from urllib.parse import urlencode
from abc import ABCMeta, abstractmethod

from unified.models import nova, hpc
from utils import array_to_dict


logger = logging.getLogger(__name__)


class NotFoundError(Exception):
    pass


class Client(object):
    """RESTful APIs' client"""

    def __init__(self, url, token=None):
        self.end_point = url
        self.headers = None
        if token:
            self.headers = {'x-ersa-auth-token': token}

    def group(self, ids, size=10):
        """Slice uuids into managable chunks for optimising request performance"""
        return [ids[i:i + size] for i in range(0, len(ids), size)]

    def _verify(self, rst):
        """Check the response for verifying existence"""
        if rst.status_code < 300:
            return True
        else:
            logger.error("Request to %s failed. HTTP error code = %d" % (self.end_point, rst.status_code))
            return False

    def get(self, path='', args={}):
        url = self.end_point + path

        query_string = urlencode(args)
        url = url + '?' + query_string if query_string else url
        logger.debug(url)

        req = requests.get(url, headers=self.headers)
        if self._verify(req):
            j = req.json()
            logger.debug(j)
            return j

        return None


class BmanClient(Client):
    """Client of Bman"""
    def __init__(self, url, token=None):
        super().__init__(url, token)
        self.organisations = {}  # cache for any organisation(account) name
        self.top_orgs = [org['id'] for org in self.get('/organisation/', {'method': 'get_tops'})]

    def get_parent_org_ids(self, role_id):
        """Gets the organisation names of a role"""
        # FIXME: Bman for MS Dynamics does not support this feature 20170428
        # A role only has the lowest organisation, for grouping, it needs
        # to be expanded into a full list
        # parents can be more han one at the same level
        orgs = []
        role = self.get('/role/%d/' % role_id)
        if role:
            parent_org_ids = self.get('/organisation/%d/get_parent_ids/' % role['organisation'])
            if parent_org_ids:
                orgs = parent_org_ids
                orgs.append(role['organisation'])
            else:
                orgs = [role['organisation']]
        return orgs

    def get_org_name(self, org_id):
        # TODO: Bman for MS Dynamics does not support this query yet 20170428
        name = ''
        if org_id in self.organisations:
            name = self.organisations[org_id]
        else:
            org = self.get('/organisation/%d/' % org_id)
            if org:
                name = self.organisations[org_id] = org['name']
        return name

    def get_org_names(self, org_ids):
        names = []
        for org_id in org_ids:
            names.append(self.get_org_name(org_id))
        return names

    def get_managing_org_names(self, role_id, billing_org_id=None):
        """
        Gets names of managing organisations of a role

        As service can be billed differently to the associated organisation,
        use billing_org_id to make sure for billing purpose, managing
        organisations are correct.
        """
        # FIXME: This may not be needed for Dynamics as there is no need to get managing org by manager role as for Bman
        names = []
        parent_org_ids = self.get_parent_org_ids(role_id)
        if parent_org_ids:
            top_count = sum([1 for org_id in parent_org_ids if org_id in self.top_orgs])
            if billing_org_id:
                names = [self.get_org_name(billing_org_id)]
                if top_count == 1 and billing_org_id in parent_org_ids:
                    # valid fully expanded organisation chain
                    names = self.get_org_names(parent_org_ids)
            else:
                # this cloud be less accurate as there may be more than one top organisation
                names = self.get_org_names(parent_org_ids)
        return names


class Usage(metaclass=ABCMeta):
    """Abstract class of calculating usage of a service in a time period"""

    # Derived classes cross-reference BMAN for classifiers:
    # university and school. If none of them found, manager field
    # is an empty list.
    def __init__(self, start, end, **kwargs):
        # conditions are used to define how to prepare data
        # source, model.
        # Derived classes may have their own set up conditions:
        # usage model, prices(?), etc
        self.start_timestamp = start
        self.end_timestamp = end
        logger.debug("Query arguments: start=%s, end=%s" % (self.start_timestamp, self.end_timestamp))
        # useage_meta is a dict with keys of the values of usage data's identifier:
        # for NECTAR, it is OpenstackID, HPC, it is owner
        self.usage_meta = None

    @abstractmethod
    def prepare(self):
        """Gets data from source, do other preparations

           It should returns manager_field
        """
        pass

    def _get_managing_orgs_of(self, identifier, unit_name='managerunit'):
        """Override parent class method as the usage meta is not from Orders"""
        managing_orgs = []
        if identifier in self.usage_meta:
            managing_orgs = [self.usage_meta[identifier]['biller']]
            if unit_name in self.usage_meta[identifier]:
                managing_orgs.append(self.usage_meta[identifier][unit_name])
        return managing_orgs

    def _get_managing_orgs(self, identifiers):
        """Gets names of managing organisation of identifiers.

           identifiers: a list with unique values which will be used by checker
           getter: a function to get the names
        """
        managers = {}
        for ident in identifiers:
            try:
                managers[ident] = self._get_managing_orgs_of(ident)
            except NotFoundError:
                logger.warning('Cannot retrieve organisation information for identifier %s' % ident)
                managers[ident] = []
            except Exception:
                managers[ident] = []
                logger.exception('Cannot retrieve organisation information for identifier %s' % ident)

        return managers

    def save(self, data):
        """Saves data into a JSON file named as ServiceUsage_StartTimestamp_EndTimestamp.json"""
        file_name = '%s_%d_%d.json' % (self.__class__.__name__, self.start_timestamp, self.end_timestamp)
        with open(file_name, 'w') as jf:
            json.dump(data, jf)
        return file_name

    def calculate(self):
        # Get data by calling prepare and inserting manager field with managing organisations
        items, manager_field = self.prepare()
        logger.debug('%d data has been returned by prepare. manager_field is %s' % (len(items), manager_field))

        # managers is the short of managing organisations: always biller and managerunit (school)
        # person manager is not included on 20170428.
        managers = self._get_managing_orgs(set(item[manager_field] for item in items))
        for item in items:
            item['manager'] = managers[item[manager_field]]

        return self.save(items)


class NovaUsage(Usage):
    """Calculates Nova usage (states) in a time period."""

    def __init__(self, start, end, crm_client, workers=1):
        super().__init__(start, end)
        self.crm_client = crm_client
        # usage_meta is a dict which has OpenstackID as key and all other information of a product (Nectar Allocation)
        self.usage_meta = array_to_dict(self.crm_client.get('/v2/contract/nectarcloudvm/'), 'OpenstackID')
        self.concurrent_workers = workers

    def _get_state(self, instance_id):
        instance = nova.Instance.query.get(instance_id)
        return instance.latest_state(self.start_timestamp, self.end_timestamp)

    def prepare(self):
        q = nova.Summary(self.start_timestamp, self.end_timestamp)
        ids = q.value()
        logger.debug("Total number of instances = %d" % len(ids))

        if len(ids):
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrent_workers) as executor:
                fs = {executor.submit(self._get_state, instance_id):
                      instance_id for instance_id in ids}
                return [fu.result() for fu in concurrent.futures.as_completed(fs)], 'tenant'
        else:
            return [], 'tenant'


class HpcUsage(Usage):
    """Calculates HPC Usage in a time period."""

    def __init__(self, start, end, crm_client):
        super().__init__(start, end)
        self.crm_client = crm_client
        self.usage_meta = array_to_dict(self.crm_client.get('/access/'), 'username')

    def _get_managing_orgs_of(self, identifier):
        """Override parent method as the usage meta is not from Orders,
           therefore there is no managerunit but unit as the secondary
        """
        return super()._get_managing_orgs_of(identifier, 'unit')

    def prepare(self):
        return hpc.Job.list(self.start_timestamp, self.end_timestamp), 'owner'
