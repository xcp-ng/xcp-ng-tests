import pytest

import logging

from packaging import version

from lib.host import Host
from lib.pool import Pool
from lib.snapshot import Snapshot
from lib.vm import VM

from typing import Generator

@pytest.fixture(scope='module')
def pool_without_uefi_certs(host: Host) -> Generator[Pool, None, None]:
    assert host.xcp_version < version.parse("8.3"), "fixture only relevant on XCP-ng 8.2"
    pool = host.pool

    # Save the certs.
    # This may fail if the pool certs are not in a consistent state, and prevent the tests from running.
    pool.save_uefi_certs()

    # clear certs in XAPI and on disk
    pool.clear_uefi_certs()

    yield pool

    # restore the saved certs (or the absence of certs)
    pool.restore_uefi_certs()

@pytest.fixture(scope='module')
def uefi_vm_and_snapshot(uefi_vm: VM) -> Generator[tuple[VM, Snapshot], None, None]:
    vm = uefi_vm

    # Any VM that has been booted at least once comes with some
    # UEFI variable state, so simply clear the state of
    # secure boot specific variables
    vm.set_uefi_setup_mode()
    logging.info('Set platform.secureboot to false for VM')
    vm.param_set('platform', False, key='secureboot')
    snapshot = vm.snapshot()

    yield vm, snapshot

    snapshot.destroy()
