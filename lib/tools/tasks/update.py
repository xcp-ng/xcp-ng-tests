"""Update tasks.

This module is intended for performing update actions on existing remote targets.
"""
import sys
from concurrent.futures import ThreadPoolExecutor

from lib.common import HostAddress
from lib.host import Host
from lib.pool import Pool
from lib.tools import logger

from typing import List

def update_all(hosts: List[HostAddress], enablerepos: List[str]):
    """Updates all targets scenario.

    This function describes the update scenario for a set of remote targets. Those targets used for
    running xcp-ng tests suite. See below.

    The scenario
    ------------

    * Receives a list of host, :py:class:`lib.common.HostAddress` (either hostname or ip address)
    * Optionally receives a list of repositories to enabled (related to `yum`)
        *repo(s) will be enabled for each host*
    * Creates a list of :py:class:`lib.pool.Pool`

    .. note:: Host must be a master

        If provided hosts are not master (or main), an error :py:exc:`AssertionError` will be thrown
        and then exits the program.

    * Updates are done using multithreading

    :param :py:class:`lib.common.HostAddress`: A list of host for the update scenario.
    :param List[str] enablerepos: Enable one or more repo(s) when updating.
    """
    logger.debug(f"Received hosts argument: {hosts}")
    logger.debug(f"Received repo(s) argument: {enablerepos}")
    # init related pools
    try:
        pools = [Pool(h) for h in hosts]
        logger.info("Preparing Pools...")
    except AssertionError as ae:
        logger.critical(ae)
        sys.exit(1)

    with ThreadPoolExecutor() as executor:
        for p in pools:
            executor.submit(update_host, p.master, enablerepos)

    logger.info(pools)


def update_host(host: Host, enablerepos: List[str] = []):
    """Updates the target.

    Updating a remote host target has the following workflow:

    * Cleans cached metadata
    * Updates packages (with optional repositories enabled or not)
    * Reboots (verifies whether the host is up or not)

    :param :py:class:`lib.host.Host` host: Target host to update.
    :param List[str] enablerepos: Extra repo(s) to enable (default: []).
    """
    logger.info(f"[{host}] Begin updating target host")

    host.yum_clean_metadata()
    host.yum_update(enablerepos=enablerepos)
    # Everything's ok, just reboot
    host.reboot(verify=True)

    logger.info(f"[{host}] Updated!")
