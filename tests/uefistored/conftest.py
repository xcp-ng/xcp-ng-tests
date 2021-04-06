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
