from __future__ import annotations

import pytest

import json
import logging
from contextlib import contextmanager

from lib import config
from lib.common import safe_split

# explicit import for package-scope fixtures
from pkgfixtures import (
    _xfs_config_on_hostA2,
    _xfs_config_on_hostB1,
    hostA2_with_xfsprogs,
    hostB1_with_xfsprogs,
    pool_with_saved_yum_state,
    xfs_sr_on_hostA2,
    xfs_sr_on_hostB1,
)

from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from lib.host import Host
    from lib.sr import SR
    from lib.vdi import VDI
    from lib.vm import VM

@pytest.fixture(params=["thin"], scope="session")
def provisioning_type(request: pytest.FixtureRequest) -> str:
    return request.param

@pytest.fixture(scope='module')
def vdi_on_linstor_sr(linstor_sr: SR) -> Generator[VDI, None, None]:
    vdi = linstor_sr.create_vdi('LINSTOR-VDI-test', virtual_size=config.volume_size)
    yield vdi
    vdi.destroy()

@contextmanager
def _vm_on_linstor_sr(host: Host, linstor_sr: SR, vm_ref: str) -> Generator[VM]:
    """
    Context manager to provide the fixture lifecycle on a VM on a Linstor SR
    with different scopes without repeating the code.
    """
    vm = host.import_vm(vm_ref, sr_uuid=linstor_sr.uuid)
    try:
        yield vm
    finally:
        logging.info("<< Destroy VM")
        vm.destroy(verify=True)

@pytest.fixture(scope='module')
def vm_on_linstor_sr(host: Host, linstor_sr: SR, vm_ref: str) -> Generator[VM, None, None]:
    with _vm_on_linstor_sr(host, linstor_sr, vm_ref) as vm:
        yield vm

@pytest.fixture(scope='function')
def vm_on_linstor_sr_function(host: Host, linstor_sr: SR, vm_ref: str) -> Generator[VM]:
    with _vm_on_linstor_sr(host, linstor_sr, vm_ref) as vm:
        yield vm
