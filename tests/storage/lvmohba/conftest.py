from __future__ import annotations

import pytest

import logging

from lib import config
from lib.host import Host
from lib.sr import SR
from lib.vdi import VDI, ImageFormat
from lib.vm import VM

# explicit import for package-scope fixtures
from pkgfixtures import (
    _xfs_config_on_hostA2,
    _xfs_config_on_hostB1,
    hostA2_with_xfsprogs,
    hostB1_with_xfsprogs,
    xfs_sr_on_hostA2,
    xfs_sr_on_hostB1,
)

from typing import Generator

@pytest.fixture(scope='package')
def lvmohba_device_config() -> dict[str, str]:
    return config.sr_device_config("LVMOHBA_DEVICE_CONFIG")

@pytest.fixture(scope='package')
def lvmohba_sr(
    host: Host, lvmohba_device_config: dict[str, str], image_format: ImageFormat
) -> Generator[SR, None, None]:
    """ A lvmohba SR on first host. """
    sr = host.sr_create('lvmohba', "lvmohba-SR-test",
                        lvmohba_device_config | {'preferred-image-formats': image_format}, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture()
def vdi_on_lvmohba_sr(lvmohba_sr: SR) -> Generator[VDI, None, None]:
    vdi = lvmohba_sr.create_vdi('lvmohba-VDI-test', virtual_size=config.volume_size)
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_lvmohba_sr(host: Host, lvmohba_sr: SR, vm_ref: str) -> Generator[VM, None, None]:
    vm = host.import_vm(vm_ref, sr_uuid=lvmohba_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
