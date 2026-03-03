"""Update Infrastructure

This module contains helpful functions to update
xcp-ng infrastructure.
"""
from infra import logger
from lib.host import Host

from typing import List

def update_target(host: Host, enablerepos: List[str] = []):
    """Update the target.

    Performs several operations to clean and update
    remote target.

    :param host: Target host to update.
    :param enablerepos: Extra repo(s) to enable (default: []).
    """
    logger.info(f"> [{host}] Begin updating target host")
    # TODO: performance and output improvements
    #     Following operations run for a long time
    #     Without 'debug mode', it is not possible
    #     to see whether operations are running or not
    host.yum_clean_metadata()
    host.yum_update_enablerepos(enablerepos=enablerepos)

    # if everything's ok, just reboot
    host.reboot(verify=True)

    logger.info(f"> [{host}] Updated!")
