import logging
import pytest

from packaging import version

@pytest.fixture(scope='module')
def pool_without_uefi_certs(host):
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
def uefi_vm_and_snapshot(uefi_vm):
    vm = uefi_vm

    # Any VM that has been booted at least once comes with some
    # UEFI variable state, so simply clear the state of
    # secure boot specific variables
    vm.set_uefi_setup_mode()
    logging.info('Set platform.secureboot to false for VM')
    vm.param_set('platform', 'secureboot', False)
    snapshot = vm.snapshot()

    yield vm, snapshot

    snapshot.destroy()
