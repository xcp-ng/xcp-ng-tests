import pytest

@pytest.fixture(scope='module')
def imported_sb_vm(imported_vm):
    vm = imported_vm

    if not vm.is_uefi:
        pytest.skip('imported_sb_vm can only be used on UEFI VMs')

    tmp_pk = vm.host.ssh(['mktemp'])
    tmp_kek = vm.host.ssh(['mktemp'])
    tmp_db = vm.host.ssh(['mktemp'])
    tmp_dbx = vm.host.ssh(['mktemp'])

    vm.host.ssh(['mv', '/usr/share/uefistored/PK.auth', tmp_pk], check=False)
    vm.host.ssh(['mv', '/var/lib/uefistored/db.auth', tmp_db], check=False)
    vm.host.ssh(['mv', '/var/lib/uefistored/KEK.auth', tmp_kek], check=False)
    vm.host.ssh(['mv', '/var/lib/uefistored/dbx.auth', tmp_dbx], check=False)

    # Any VM that has been booted at least once comes with some
    # UEFI variable state, so simply clear the state of
    # secure boot specific variables before each test.
    vm.host.ssh(['varstore-sb-state', vm.uuid, 'setup'])

    yield vm

    # Restore PK, KEK, db on host
    vm.host.ssh(['mv', tmp_pk, '/usr/share/uefistored/PK.auth'], check=False)
    vm.host.ssh(['mv', tmp_kek, '/var/lib/uefistored/KEK.auth'], check=False)
    vm.host.ssh(['mv', tmp_db, '/var/lib/uefistored/db.auth'], check=False)
    vm.host.ssh(['mv', tmp_dbx, '/var/lib/uefistored/dbx.auth'], check=False)

@pytest.fixture(scope='module')
def running_linux_uefi_vm(imported_vm):
    """
    A fixture to provide a Linux UEFI VM.

    The only reason that this fixture exists is that booting up a VM is a waste
    of time if you are going to just skip a test because it is not UEFI or
    Linux. For that reason, this fixture checks that the VM is UEFI and Linux
    before it boots the VM.
    """
    vm = imported_vm

    if not vm.is_uefi:
        pytest.skip("must be a UEFI VM")
    if vm.is_windows:
        pytest.skip("must be a Linux VM")

    vm.start()
    vm.wait_for_os_booted()

    yield vm
