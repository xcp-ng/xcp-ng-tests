"""Infra package

This package provides tools to help automating tasks on
xcp-ng VMs.
"""

import logging

logger = logging.getLogger("xcpng-infra")
logging.basicConfig(format='%(asctime)s > %(levelname)s > %(message)s',
                    level=logging.INFO)
