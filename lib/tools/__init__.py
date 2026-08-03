"""Tools for automation.

This sub package is intended for scripting and automation tasks.
Code is sometimes too long to be written as a standalone script
(e.g. like ``scripts`` directory in root project).
"""

import logging

from lib.logger import setup_colored_logging

logger = logging.getLogger()
setup_colored_logging()
