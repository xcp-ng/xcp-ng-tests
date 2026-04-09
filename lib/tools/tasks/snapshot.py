"""Snapshot tasks.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from lib.host import Host
from lib.vm import VM

from .. import logger

def create_snapshot(host: Host, vm_uuids: list[str]):
    """Create a snapshot for a list of VMs in host.

    :param `lib.Host` host:
        The target parent which hosts the VMs.
    :param list[str] vm_uuids:
        uuids of target VMs.
    """
    # init VMs list
    vms = [VM(uuid, host) for uuid in vm_uuids]

    snapshot_name = f"utd-{date.today().strftime('%Y%m%d')}"

    logger.debug(f"[{host}] Create snapshot '{snapshot_name}' for VMs: {vm_uuids}")

    with ThreadPoolExecutor() as executor:
        for vm in vms:
            executor.submit(vm.snapshot, None, snapshot_name)
