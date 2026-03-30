"""Update tasks.

This module is intended for performing update actions on existing remote targets.
"""
from concurrent.futures import ThreadPoolExecutor

from lib.host import Host
from lib.pool import Pool

from .. import logger

def update_all(inventory: dict) -> None:
    """Updates all master (primary) hosts.

    .. note:: Host must be a master

        Throws error if hosts are not master (primary).

    :param dict inventory:
        Each host (key) holds its own config data (values).
    """
    logger.debug(f"Inventory: {inventory}")
    # init related pools
    pools = [Pool(h) for h in inventory]

    with ThreadPoolExecutor() as executor:
        for p in pools:
            executor.submit(update_host, p.master, inventory[p.master.hostname_or_ip]["enablerepos"])


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
