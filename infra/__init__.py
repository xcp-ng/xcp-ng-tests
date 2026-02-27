"""Infra package

This package provides tools to help automating tasks on
xcp-ng VMs.
"""

import logging

logger = logging.getLogger()
logging.basicConfig(style="{", format="{asctime} - {levelname:<8} - {module}.{funcName} - {message}",
                    level=logging.INFO)
