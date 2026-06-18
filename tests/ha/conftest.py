from __future__ import annotations

import logging
from typing import Generator

import pytest

from lib import config
from lib.host import Host
from lib.sr import SR
from lib.vdi import ImageFormat
from lib.vm import VM
from tests.ha.ha import destroy_ha_protected_vm, destroy_nfs_sr_after_ha

@pytest.fixture(scope='package')
def nfs_device_config() -> dict[str, str]:
    return config.sr_device_config('NFS_DEVICE_CONFIG')

@pytest.fixture(scope='package')
def nfs_sr(host: Host, image_format: ImageFormat, nfs_device_config: dict[str, str]) -> Generator[SR, None, None]:
    """A shared NFS SR (VM disks + HA heartbeat)."""
    sr = host.sr_create(
        'nfs', 'NFS-SR-test', nfs_device_config | {'preferred-image-formats': image_format}, shared=True
    )
    yield sr
    logging.info('<< Destroy NFS SR %s after HA tests', sr.uuid)
    try:
        destroy_nfs_sr_after_ha(host.pool, sr)
    except Exception:
        logging.error('Could not destroy NFS SR %s after HA tests', sr.uuid, exc_info=True)
        raise

@pytest.fixture(scope='module')
def ha_protected_vm(host: Host, nfs_sr: SR, vm_ref: str) -> Generator[VM, None, None]:
    """HA-protected VM reused across scenarios."""
    vm = host.import_vm(vm_ref, sr_uuid=nfs_sr.uuid)
    yield vm
    logging.info('<< Destroy HA protected VM %s', vm.uuid)
    try:
        destroy_ha_protected_vm(host.pool, vm)
    except Exception:
        logging.error('Could not destroy HA protected VM %s', vm.uuid, exc_info=True)
        raise
