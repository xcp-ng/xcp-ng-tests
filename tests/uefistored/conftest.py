import logging
import pytest

@pytest.fixture(scope='module')
def pool_without_uefi_certs(host):
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
    logging.info('Clear VM UEFI certs and set SB to false')
    vm.host.ssh(['varstore-sb-state', vm.uuid, 'setup'])
    vm.param_set('platform', 'secureboot', False)
    snapshot = vm.snapshot()

    yield vm, snapshot

    snapshot.destroy()

@pytest.fixture(scope='module')
def unix_uefi_vm(unix_vm, uefi_vm):
    return uefi_vm

@pytest.fixture(scope='module')
def unix_uefi_vm_and_snapshot(unix_vm, uefi_vm_and_snapshot):
    yield uefi_vm_and_snapshot
