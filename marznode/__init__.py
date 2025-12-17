"""Initiate logger for marznode"""

import logging

from marznode import config

# Настраиваем корневой логгер, чтобы все дочерние модули получили handler
logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)

logger = logging.getLogger(__name__)
