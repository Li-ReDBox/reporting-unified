# reporting-unified [![Build Status](https://travis-ci.org/eResearchSA/reporting-unified.svg)](https://travis-ci.org/eResearchSA/reporting-unified)
A bunch of small Flask applications for accessing eRSA reporting databases.

The package only works for PostgreSql because it uses:

* UUID
* INET
* MACADDR

0. run `sudo -u postgres psql -f prepare_db.sql`
0. Update config.py
0. activate env
0. bin/ersa_reporting-prep ersa_reporting.PACKAGE

##Deployment

The package can be served by, for example, __nginx__ (proxy) + __gunicorn__.

If deploy on a CentOS 7 cloud instance, [script](centos7.sh) can be used to set up __nginx__ and __gunicorn__.

The packge needs to talk to a database so there is a configuration file for __gunicorn__ to use
which is listed below in the example of __hnas.conf__. It also can be generated by a script like
[gconf_example](gconf_generator.sh.example).

The log of an application is currently hard-coded to be saved in `/var/log/gunicorn/` which assumes `gunicorn` is configured to save logs to there.

An application's log is named as ersa_reporting._application_.log, e.g. __'ersa_reporting.hnas.log'__.

One example shown here assumes package has been installed in `/usr/lib/ersa_reporting` in a virtual environment in `unified_api_env`
and application `hnas` is being served by these commands:

###run gunicorn

```shell
PDIR=/usr/lib/ersa_reporting
cd $PDIR
source unified_api_env/bin/activate
unified_api_env/bin/gunicorn -c hnas.conf hnas:app
```

__conf file `hnas.conf`__

```python

raw_env = ["ERSA_REPORTING_PACKAGE=hnas", "ERSA_DEBUG=True",
           "ERSA_DATABASE_URI=postgresql://user:pass@host/db",
           "ERSA_AUTH_TOKEN=DEBUG_TOKEN"]
timeout = 7200
proc_name = "hnas"
workers = 2
pidfile = "/run/gunicorn/hnas.pid"
accesslog = "/var/log/gunicorn/hnas_access.log"
errorlog = "/var/log/gunicorn/hnas_error.log"
loglevel = "info"
```
