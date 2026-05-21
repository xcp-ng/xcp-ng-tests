"""Update tasks.

This module is intended for performing update actions on existing remote targets.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from lib.host import Host
from lib.pool import NotAMasterHostError, Pool
from lib.tools.inventory import Inventory

from .. import logger

def update_pools(inventory: Inventory) -> None:
    """Updates hosts in pool(s).

    .. note::

        Every non-master hosts in inventory will be ignored

    *Update each pool's master host declared in inventory first, then, update other hosts for each pool.*

    :param dict inventory:
        Each host (key) holds its own config data (values, eg: `enablerepos`).
    """
    logger.debug(f"Inventory: {inventory}")
    inventory_hosts = inventory["hosts"]
    # init related pools
    pools: list[Pool] = []
    nested_hosts: dict[str, list[Host]] = {}
    for host in inventory_hosts:
        try:
            p = Pool(host)
            pools.append(p)
            hosting_pool = inventory_hosts[host]["hosting_pool"]
            if hosting_pool is not None:
                # we assume all hosts are nested, not only master
                if nested_hosts.get(hosting_pool) is not None:
                    nested_hosts[hosting_pool].extend(p.hosts)
                else:
                    nested_hosts[hosting_pool] = p.hosts
        except NotAMasterHostError:
            logger.warning(f"[{host}] Skipping: not a master host")

    # update master hosts
    with ThreadPoolExecutor() as executor:
        for p in pools:
            executor.submit(p.master.update, inventory_hosts[p.master.hostname_or_ip]["repositories"])

    # update other hosts
    with ThreadPoolExecutor() as executor:
        for p in pools:
            # omit first item because it is the pool's master
            for other_host in p.hosts[1:]:
                # repos are the same as for the master host
                repos = inventory_hosts[p.master.hostname_or_ip]["repositories"]
                executor.submit(other_host.update, repos)
