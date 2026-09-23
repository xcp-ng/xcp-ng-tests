"""Cleanup tasks.

This module is intended for removing all VMs and all VDIs on local storage
from existing remote targets.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from lib.common import safe_split, wait_for_not
from lib.pool import NotAMasterHostError, Pool
from lib.sr import SR
from lib.tools.inventory import Inventory
from lib.vdi import VDI
from lib.vm import VM

from .. import logger

def clean_pools(inventory: Inventory, dry_run: bool = False) -> int:
    """Remove all VMs and all orphan VDIs on local storage from pool(s).

    .. note::

        Every non-master hosts in inventory will be ignored

    *For each pool's master host declared in inventory, destroy all its VMs
    (with all their VDIs, regardless of their location), then destroy all the
    orphan VDIs left on the local (non-shared) SRs.*

    :param Inventory inventory:
        Each host (key) holds its own config data (values, eg: `enablerepos`).
    :param bool dry_run:
        When True, only log what would be removed without actually deleting.
    :return:
        The number of VMs/VDIs/snapshots that failed to be removed.
    """
    inventory_hosts = inventory["hosts"]
    pools: list[Pool] = []
    for host in inventory_hosts:
        try:
            pools.append(Pool(host))
        except NotAMasterHostError:
            logger.warning(f"[{host}] Skipping: not a master host")

    with ThreadPoolExecutor() as executor:
        futures = {executor.submit(clean_pool, p, dry_run): p for p in pools}
        failures = 0
        for future in futures:
            pool = futures[future]
            try:
                failures += future.result()
            except Exception as exc:
                logger.error(f"Cleaning pool has failed! The master {pool.master} cannot be cleaned: {exc}")
                failures += 1
    return failures

def clean_pool(pool: Pool, dry_run: bool) -> int:
    """Remove all VMs and all orphan VDIs on local SRs from a single pool.

    :return:
        The number of VMs plus number VDIs that failed to be removed from pool
    """
    master = pool.master
    log_prefix = 'Would remove' if dry_run else 'Removing'
    failures = 0

    vm_uuids = safe_split(master.xe(
        'vm-list',
        {'is-control-domain': False, 'is-a-template': False},
        minimal=True,
    )) + safe_split(master.xe(
        'vm-list',
        {'is-a-snapshot': True},
        minimal=True,
    ))
    for vm_uuid in vm_uuids:
        vm = VM(vm_uuid, master)
        logger.info(f"[{master}] {log_prefix} VM {vm.uuid} ({vm.name()})")
        if not dry_run:
            try:
                vm.destroy(verify=True)
            except Exception as exc:
                logger.error(f"[{master}] Failed to remove VM {vm.uuid} ({vm.name()}): {exc}")
                failures += 1

    sr_uuids = local_sr_uuids(pool)
    for sr_uuid in sr_uuids:
        for vdi_uuid in SR(sr_uuid, pool).vdi_uuids(managed=True):
            vdi = VDI(vdi_uuid, sr=SR(sr_uuid, pool))
            logger.info(f"[{master}] {log_prefix} orphan VDI {vdi.uuid} from local SR {sr_uuid}")
            if not dry_run:
                try:
                    vdi.destroy()
                except Exception as exc:
                    logger.error(f"[{master}] Failed to destroy VDI {vdi.uuid}: {exc}")
                    failures += 1

    if not dry_run and failures == 0:
        for sr_uuid in sr_uuids:
            wait_for_not(
                lambda: len(SR(sr_uuid, pool).vdi_uuids(managed=True)) > 0,
                f"Wait for local SR {sr_uuid} to be empty",
            )
    return failures

def local_sr_uuids(pool: Pool) -> list[str]:
    """Return the UUIDs of the pool's local (non-shared, user) SRs."""
    uuids = safe_split(pool.master.xe('sr-list', {'content-type': 'user'}, minimal=True))
    return [uuid for uuid in uuids if not SR(uuid, pool).is_shared()]
