import platform
import schedule
from time import sleep
from queue import Queue
from sys import version
from threading import Thread
from os import access, R_OK, getenv
from distro import linux_distribution
from os.path import isdir, abspath, dirname, join
from argparse import ArgumentParser, RawTextHelpFormatter
from logging import getLogger, StreamHandler, Formatter, DEBUG

# Needed to check version of python
from varken import structures  # noqa
from varken.ombi import OmbiAPI
from varken.unifi import UniFiAPI
from varken import VERSION, BRANCH
from varken.sonarr import SonarrAPI
from varken.radarr import RadarrAPI
from varken.iniparser import INIParser
from varken.dbmanager import DBManager
from varken.helpers import GeoIPHandler
from varken.tautulli import TautulliAPI
from varken.sickchill import SickChillAPI
from varken.varkenlogger import VarkenLogger


PLATFORM_LINUX_DISTRO = ' '.join(x for x in linux_distribution() if x)


def thread():
    while schedule.jobs:
        job = QUEUE.get()
        a = job()
        if a is not None:
            schedule.clear(a)
        QUEUE.task_done()


if __name__ == "__main__":
    parser = ArgumentParser(prog='varken',
                            description='Command-line utility to aggregate data from the plex ecosystem into InfluxDB',
                            formatter_class=RawTextHelpFormatter)

    parser.add_argument("-d", "--data-folder", help='Define an alternate data folder location')
    parser.add_argument("-D", "--debug", action='store_true', help='Use to enable DEBUG logging')

    opts = parser.parse_args()

    DATA_FOLDER = abspath(join(dirname(__file__), 'data'))

    templogger = getLogger('temp')
    templogger.setLevel(DEBUG)
    tempch = StreamHandler()
    tempformatter = Formatter('%(asctime)s : %(levelname)s : %(module)s : %(message)s', '%Y-%m-%d %H:%M:%S')
    tempch.setFormatter(tempformatter)
    templogger.addHandler(tempch)

    if opts.data_folder:
        ARG_FOLDER = opts.data_folder

        if isdir(ARG_FOLDER):
            DATA_FOLDER = ARG_FOLDER
            if not access(DATA_FOLDER, R_OK):
                templogger.error("Read permission error for %s", DATA_FOLDER)
                exit(1)
        else:
            templogger.error("%s does not exist", ARG_FOLDER)
            exit(1)

    # Set Debug to True if DEBUG env is set
    enable_opts = ['True', 'true', 'yes']
    debug_opts = ['debug', 'Debug', 'DEBUG']

    if not opts.debug:
        opts.debug = True if any([getenv(string, False) for true in enable_opts
                                  for string in debug_opts if getenv(string, False) == true]) else False

    # Initiate the logger
    vl = VarkenLogger(data_folder=DATA_FOLDER, debug=opts.debug)
    vl.logger.info('Starting Varken...')

    vl.logger.info('Data folder is "%s"', DATA_FOLDER)

    vl.logger.info(u"%s %s (%s%s)", platform.system(), platform.release(), platform.version(),
                   ' - ' + PLATFORM_LINUX_DISTRO if PLATFORM_LINUX_DISTRO else '')

    vl.logger.info(u"Python %s", version)

    vl.logger.info("Varken v%s-%s", VERSION, BRANCH)

    CONFIG = INIParser(DATA_FOLDER)
    DBMANAGER = DBManager(CONFIG.influx_server)
    QUEUE = Queue()

    if CONFIG.sonarr_enabled:
        for server in CONFIG.sonarr_servers:
            SONARR = SonarrAPI(server, DBMANAGER)
            if server.queue:
                at_time = schedule.every(server.queue_run_seconds).seconds
                at_time.do(QUEUE.put, SONARR.get_queue).tag("sonarr-{}-get_queue".format(server.id))
            if server.missing_days > 0:
                at_time = schedule.every(server.missing_days_run_seconds).seconds
                at_time.do(QUEUE.put, SONARR.get_missing).tag("sonarr-{}-get_missing".format(server.id))
            if server.future_days > 0:
                at_time = schedule.every(server.future_days_run_seconds).seconds
                at_time.do(QUEUE.put, SONARR.get_future).tag("sonarr-{}-get_future".format(server.id))

    if CONFIG.tautulli_enabled:
        GEOIPHANDLER = GeoIPHandler(DATA_FOLDER)
        schedule.every(12).to(24).hours.do(QUEUE.put, GEOIPHANDLER.update)
        for server in CONFIG.tautulli_servers:
            TAUTULLI = TautulliAPI(server, DBMANAGER, GEOIPHANDLER)
            if server.get_activity:
                at_time = schedule.every(server.get_activity_run_seconds).seconds
                at_time.do(QUEUE.put, TAUTULLI.get_activity).tag("tautulli-{}-get_activity".format(server.id))
            if server.get_stats:
                at_time = schedule.every(server.get_stats_run_seconds).seconds
                at_time.do(QUEUE.put, TAUTULLI.get_stats).tag("tautulli-{}-get_stats".format(server.id))

    if CONFIG.radarr_enabled:
        for server in CONFIG.radarr_servers:
            RADARR = RadarrAPI(server, DBMANAGER)
            if server.get_missing:
                at_time = schedule.every(server.get_missing_run_seconds).seconds
                at_time.do(QUEUE.put, RADARR.get_missing).tag("radarr-{}-get_missing".format(server.id))
            if server.queue:
                at_time = schedule.every(server.queue_run_seconds).seconds
                at_time.do(QUEUE.put, RADARR.get_queue).tag("radarr-{}-get_queue".format(server.id))

    if CONFIG.ombi_enabled:
        for server in CONFIG.ombi_servers:
            OMBI = OmbiAPI(server, DBMANAGER)
            if server.request_type_counts:
                at_time = schedule.every(server.request_type_run_seconds).seconds
                at_time.do(QUEUE.put, OMBI.get_request_counts).tag("ombi-{}-get_request_counts".format(server.id))
            if server.request_total_counts:
                at_time = schedule.every(server.request_total_run_seconds).seconds
                at_time.do(QUEUE.put, OMBI.get_all_requests).tag("ombi-{}-get_all_requests".format(server.id))
            if server.issue_status_counts:
                at_time = schedule.every(server.issue_status_run_seconds).seconds
                at_time.do(QUEUE.put, OMBI.get_issue_counts).tag("ombi-{}-get_issue_counts".format(server.id))

    if CONFIG.sickchill_enabled:
        for server in CONFIG.sickchill_servers:
            SICKCHILL = SickChillAPI(server, DBMANAGER)
            if server.get_missing:
                at_time = schedule.every(server.get_missing_run_seconds).seconds
                at_time.do(QUEUE.put, SICKCHILL.get_missing).tag("sickchill-{}-get_missing".format(server.id))

    if CONFIG.unifi_enabled:
        for server in CONFIG.unifi_servers:
            UNIFI = UniFiAPI(server, DBMANAGER)
            at_time = schedule.every(server.get_usg_stats_run_seconds).seconds
            at_time.do(QUEUE.put, UNIFI.get_usg_stats).tag("unifi-{}-get_usg_stats".format(server.id))

    # Run all on startup
    SERVICES_ENABLED = [CONFIG.ombi_enabled, CONFIG.radarr_enabled, CONFIG.tautulli_enabled, CONFIG.unifi_enabled,
                        CONFIG.sonarr_enabled, CONFIG.sickchill_enabled]
    if not [enabled for enabled in SERVICES_ENABLED if enabled]:
        vl.logger.error("All services disabled. Exiting")
        exit(1)

    WORKER = Thread(target=thread)
    WORKER.start()

    schedule.run_all()

    while schedule.jobs:
        schedule.run_pending()
        sleep(1)
