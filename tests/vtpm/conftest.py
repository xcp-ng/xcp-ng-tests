import pytest

import logging

from lib.packagemanager import Package
from lib.vm import VM

from typing import Generator

@pytest.fixture(scope='module')
def halted_uefi_unix_vm(uefi_vm: VM, unix_vm: VM) -> Generator[VM, None, None]:
    assert uefi_vm.is_halted(), "The VM must be halted for these tests"
    yield uefi_vm

@pytest.fixture(scope='module')
def snapshotted_halted_uefi_unix_vm(halted_uefi_unix_vm: VM) -> Generator[VM, None, None]:
    vm = halted_uefi_unix_vm
    snapshot = vm.snapshot()

    yield vm

    try:
        snapshot.revert()
    finally:
        snapshot.destroy()

@pytest.fixture(scope='module')
def unix_vm_with_vtpm(snapshotted_halted_uefi_unix_vm: VM) -> Generator[VM, None, None]:
    vm = snapshotted_halted_uefi_unix_vm

    has_vtpm = vm.get_vtpm_uuid()
    if not has_vtpm:
        vm.create_vtpm()
    yield vm
    # Tear down
    if not has_vtpm:
        vm.destroy_vtpm()

@pytest.fixture(scope='module')
def started_unix_vm_with_vtpm(unix_vm_with_vtpm: VM) -> Generator[VM, None, None]:
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
def unix_vm_with_tpm2_tools(started_unix_vm_with_vtpm: VM) -> Generator[VM, None, None]:
    vm = started_unix_vm_with_vtpm

    pkg_mgr = vm.package_manager()
    logging.info("Installing tpm2-tools package using '%s'" % pkg_mgr.name())
    pkg_mgr.install(Package.tpm2_tools)

    yield vm
