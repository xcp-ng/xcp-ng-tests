"""Tools for automation.

This sub package is intended for scripting and automation tasks.
Code is sometimes too long to be written as a standalone script
(e.g. like ``scripts`` directory in root project).
"""

import logging

logger = logging.getLogger()
logging.basicConfig(
    format="%(asctime)s.%(msecs)03d %(levelname)s %(message)s", datefmt="%b %d %H:%M:%S", level=logging.INFO
)
