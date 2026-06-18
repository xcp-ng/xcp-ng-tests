from __future__ import annotations

import logging
from typing import Generator

import pytest

from lib.common import wait_for
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR
from lib.vm import VM
from tests.ha.ha import pool_master
from tests.storage.nfs.conftest import nfs_sr

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host in a pool with HA-capable networking
# - hostA2: second member of the pool
# From data.py:
# - NFS_DEVICE_CONFIG with a shared NFS export (used for VM disks and HA heartbeat)
# From --vm parameter:
# - a small agile linux VM importable on the NFS SR

@pytest.fixture(scope="module")
def set_ha_pool(host: Host, nfs_sr: SR) -> Generator[Pool, None, None]:
    pool = host.pool
    heartbeat_sr_uuid = nfs_sr.uuid
    logging.info("Enable pool HA (heartbeat SR %s)", heartbeat_sr_uuid)

    try:
        max_fail = host.xe("pool-ha-compute-max-host-failures-to-tolerate")
        if max_fail.strip() != "0":
            pool.param_set("ha-host-failures-to-tolerate", max_fail)
    except Exception:
        logging.warning("Could not set ha-host-failures-to-tolerate")

    host.xe("pool-ha-enable", {"heartbeat-sr-uuids": heartbeat_sr_uuid})

    wait_for(lambda: pool.param_get("ha-enabled"), "Wait for pool HA to become enabled", timeout_secs=5 * 60)
    yield pool
    logging.info("Disable pool HA")
    try:
        pool_master(pool).xe("pool-ha-disable")
    except Exception:
        logging.warning("Could not disable pool HA", exc_info=True)

@pytest.fixture(scope="module")
def ha_protected_vm(
    host: Host,
    nfs_sr: SR,
    set_ha_pool: Pool,
    vm_ref: str,
) -> Generator[VM, None, None]:
    vm = host.import_vm(vm_ref, sr_uuid=nfs_sr.uuid)
    logging.info("Set HA restart priority on protected VM %s", vm.uuid)
    vm.param_set("ha-restart-priority", "restart")
    yield vm
    logging.info("Destroy HA protected VM %s", vm.uuid)
    try:
        vm.param_clear("ha-restart-priority")
    except Exception:
        pass
    if vm.is_running():
        vm.shutdown(force=True)
    vm.destroy(verify=True)
