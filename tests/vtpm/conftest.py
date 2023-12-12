import pytest
import logging

from lib.common import PackageManagerEnum

@pytest.fixture(scope='module')
def halted_uefi_unix_vm(uefi_vm, unix_vm):
    assert uefi_vm.is_halted(), "The VM must be halted for these tests"
    yield uefi_vm

@pytest.fixture(scope='module')
def snapshotted_halted_uefi_unix_vm(halted_uefi_unix_vm):
    vm = halted_uefi_unix_vm
    snapshot = vm.snapshot()

    yield vm

    try:
        snapshot.revert()
    finally:
        snapshot.destroy()

@pytest.fixture(scope='module')
def unix_vm_with_vtpm(snapshotted_halted_uefi_unix_vm):
    vm = snapshotted_halted_uefi_unix_vm

    vm.create_vtpm()
    yield vm
    # Tear down
    vm.destroy_vtpm()

@pytest.fixture(scope='module')
def started_unix_vm_with_vtpm(unix_vm_with_vtpm):
    vm = unix_vm_with_vtpm

    vm.start()
    try:
        vm.wait_for_os_booted()
    except Exception:
        vm.shutdown(force=True, verify=True)
        raise

    yield vm
    # Tear down
    vm.shutdown(verify=True, force_if_fails=True)

@pytest.fixture(scope='module')
def unix_vm_with_tpm2_tools(started_unix_vm_with_vtpm):
    vm = started_unix_vm_with_vtpm

    pkg_mgr = vm.detect_package_manager()
    if pkg_mgr == PackageManagerEnum.APT_GET:
        # Old versions of apt-get doesn't support the --update option with the
        # install command so we have to first update then install
        cmd = ['apt-get', 'update', '&&', 'apt-get']
    elif pkg_mgr == PackageManagerEnum.RPM:
        cmd = ['yum']
    else:
        pytest.fail("Unsupported package manager for this test. Cannot install tpm2-tools")

    logging.info("Installing tpm2-tools package using '%s'" % cmd[0])
    vm.ssh(cmd + ['install', '-y', 'tpm2-tools'])

    yield vm
