import logging
import time

from config import RECONNECT_DELAY
from core.health_server import start_health_server
from core.startup_checks import check_projects_root
from core.ws_client import connect

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    check_projects_root()
    start_health_server()
    while True:
        connect()
        logger.info(f"Reconnecting in {RECONNECT_DELAY} seconds...")
        time.sleep(RECONNECT_DELAY)
