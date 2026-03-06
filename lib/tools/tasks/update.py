"""Update tasks.

This module is intended for performing update actions on existing remote targets.
"""
from concurrent.futures import ThreadPoolExecutor

from lib.common import HostAddress
from lib.host import Host
from lib.pool import Pool

from .. import logger

def update_all(master_hosts: list[HostAddress], enablerepos: list[str]) -> None:
    """Updates all master (primary) hosts.

    .. note:: Host must be a master

        Throws error if hosts are not master (primary).

    :param :py:class:`list[lib.common.HostAddress]` master_hosts:
        A list of hosts to update.
    :param list[str] enablerepos:
        Repositories to enable when updating.
    """
    logger.debug(f"[{master_hosts}] enablerepos: {enablerepos}")
    # init related pools
    pools = [Pool(h) for h in master_hosts]

    with ThreadPoolExecutor() as executor:
        for p in pools:
            executor.submit(update_host, p.master, enablerepos)


def update_host(host: Host, enablerepos: list[str] = []):
    """Updates the target host.

    An helper function that wraps update tasks on specific host.

    :param :py:class:`lib.host.Host` host:
        Target host to update.
    :param list[str] enablerepos:
        Repositories to enable when updating.
    """
    logger.info(f"[{host}] Updating...")

    host.yum_clean_metadata()
    host.yum_update(enablerepos=enablerepos)
    # Everything's ok, just reboot
    host.reboot(verify=True)

    logger.info(f"[{host}] Updated successfully!")
