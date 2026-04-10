"""Update tasks.

This module is intended for performing update actions on existing remote targets.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from lib.host import Host
from lib.pool import NotAMasterHostError, Pool
from lib.tools.inventory import Inventory
from lib.tools.tasks.snapshot import create_snapshot

from .. import logger

def update_pools(inventory: Inventory) -> None:
    """Updates hosts in pool(s).

    .. note::

        Every non-master hosts in inventory will be ignored

    *Update master hosts declared in inventory first, then, update secondary hosts attached to each master.*

    :param dict inventory:
        Each host (key) holds its own config data (values, eg: `enablerepos`).
    """
    logger.debug(f"Inventory: {inventory}")
    inventory_hosts = inventory["hosts"]
    # init related pools
    pools: list[Pool] = []
    nested_hosts: list[Host] = []
    for host in inventory_hosts:
        try:
            p = Pool(host)
            pools.append(p)
            if inventory_hosts[host]["nested"]:
                # we assume secondary are nested when master is nested
                nested_hosts.extend(p.hosts)
        except NotAMasterHostError:
            logger.warning(f"[{host}] Skipping: not a master host")

    with ThreadPoolExecutor() as executor:
        for p in pools:
            executor.submit(p.master.update, inventory_hosts[p.master.hostname_or_ip]["enablerepos"])

    # secondary hosts
    with ThreadPoolExecutor() as executor:
        for p in pools:
            # omit first item because it is a primary (master)
            for secondary in p.hosts[1:]:
                # repos are the same as the primary (master)
                repos = inventory_hosts[p.master.hostname_or_ip]["enablerepos"]
                executor.submit(secondary.update, repos)

    # Snapshot creation
    # get ids of VMs for parent host
    vm_uuids = [h.get_system_uuid() for h in nested_hosts]
    if inventory["parent"] is not None:
        parent_pool = Pool(inventory["parent"]) # mandatory for getting an host instance
        create_snapshot(parent_pool.master, vm_uuids)
