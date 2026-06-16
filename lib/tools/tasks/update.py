"""Update tasks.

This module is intended for performing update actions on existing remote targets.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from lib.host import Host
from lib.pool import NotAMasterHostError, Pool
from lib.tools.inventory import Inventory
from lib.tools.tasks.snapshot import create_snapshots

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
                if nested_hosts.get(hosting_pool) is not None:
                    nested_hosts[hosting_pool].extend([h for h in p.hosts if h.is_nested])
                else:
                    nested_hosts[hosting_pool] = [h for h in p.hosts if h.is_nested]
        except NotAMasterHostError:
            logger.warning(f"[{host}] Skipping: not a master host")

    # update master hosts
    with ThreadPoolExecutor() as executor:
        future_masters = {executor.submit(
            p.master.update, inventory_hosts[p.master.hostname_or_ip]["repositories"]): p.master for p in pools}
        for future in as_completed(future_masters):
            future_master = future_masters[future]
            try:
                future.result()
            except Exception as exc:
                logger.error(f"Updating pool has failed! The master {future_master} cannot be updated.")
                logger.info(
                    "*** Due to previous error, the pool updating task will stop. "
                    "Waiting for running updates to finish if any. ***"
                )
                raise exc

    # update other hosts
    with ThreadPoolExecutor() as executor:
        future_other_hosts = {}
        for p in pools:
            # omit first item because it is the pool's master
            for h in p.hosts[1:]:
                # repos are the same as for the master host
                repos = inventory_hosts[p.master.hostname_or_ip]["repositories"]
                future_other_hosts[executor.submit(h.update, repos)] = h
        for future in as_completed(future_other_hosts):
            other_host = future_other_hosts[future]
            try:
                future.result()
            except Exception as exc:
                logger.error(f"Updating pool has failed! The host {other_host} cannot be updated.")
                logger.info(
                    "*** Due to previous error, the pool updating task will stop. "
                    "Waiting for running updates to finish if any. ***"
                )
                raise exc

    # Snapshot creation
    for hosting_pool, nested in nested_hosts.items():
        pool = Pool(hosting_pool) # mandatory for getting an host instance
        vm_uuids = [h.get_system_uuid() for h in nested]
        create_snapshots(pool.master, vm_uuids)
