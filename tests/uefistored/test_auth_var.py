import pytest

from subprocess import CalledProcessError
from lib.efi import (
    Certificate,
    EFIAuth,
    EFI_GLOBAL_VARIABLE_GUID,
    EFI_GLOBAL_VARIABLE_GUID_STR,
    EFI_AT_ATTRS_BYTES,
    EFI_AT_ATTRS,
    EFI_GUIDS,
    EFI_GUID_STRS,
)

def set_and_assert_var(vm, cert, new, should_pass):
    var = 'myvariable'

    _, old = vm.get_efi_var(var, EFI_GLOBAL_VARIABLE_GUID_STR)

    signed = cert.sign_data(var, new, EFI_GLOBAL_VARIABLE_GUID)

    ok = True
    try:
        vm.set_efi_var(var, EFI_GLOBAL_VARIABLE_GUID_STR, EFI_AT_ATTRS_BYTES, signed)
    except CalledProcessError:
        ok = False

    _, ret = vm.get_efi_var(var, EFI_GLOBAL_VARIABLE_GUID_STR)

    if should_pass:
        assert ret == new
    else:
        assert ret == old
        assert not ok, 'This var should not have successfully set'


def test_auth_variable(imported_vm):
    vm = imported_vm
    if vm.is_windows:
        pytest.skip('not valid test for Windows VMs')

    try:
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()

        cert = Certificate()

        # Set the variable
        set_and_assert_var(vm, cert, b'I am old news', should_pass=True)

        # Set the variable with new data, signed by the same cert
        set_and_assert_var(vm, cert, b'I am new news', should_pass=True)

        # Remove var
        set_and_assert_var(vm, cert, b'', should_pass=True)

        # Set the variable with new data, signed by the same cert
        set_and_assert_var(vm, cert, b'new data', should_pass=True)

        # Set the variable with new data, signed by the same cert
        set_and_assert_var(vm, Certificate(), b'this should fail', should_pass=False)
    finally:
        if vm.is_running():
            vm.shutdown()


def set_auth(vm, auth):
    vm.host.scp(auth.auth, '/tmp/%s.auth' % auth.name)
    vm.host.ssh([
        'varstore-set', vm.uuid, EFI_GUID_STRS[auth.name], auth.name,
        str(EFI_AT_ATTRS), '/tmp/%s.auth' % auth.name
    ])


def test_db_append(imported_vm):
    """Test append of UEFI variable db."""
    vm = imported_vm
    if vm.is_windows:
        pytest.skip('not valid test for Windows VMs')

    try:
        # Clear any SB certs
        vm.host.ssh(['varstore-sb-state', vm.uuid, 'setup'])

        # Create and install certs
        PK = EFIAuth('PK')
        KEK = EFIAuth('KEK')
        db1 = EFIAuth('db')

        db2 = EFIAuth('db')
        PK.sign_auth(PK)
        PK.sign_auth(KEK)
        KEK.sign_auth(db1)
        KEK.sign_auth(db2)

        set_auth(vm, db1)
        set_auth(vm, KEK)
        set_auth(vm, PK)

        if not vm.is_running():
            vm.start()
        vm.wait_for_vm_running_and_ssh_up()

        guid = EFI_GUID_STRS['db']
        old_attrs, old_data = vm.get_efi_var('db', guid)

        vm.scp(db2.auth, '/tmp/db.auth')
        name = guid + '-db'
        vm.execute_bin('tools/efivar-static',
                       ['-n', name, '--append', '-f', '/tmp/db.auth',
                        '--attributes=' + hex(EFI_AT_ATTRS)]
                       )

        new_attrs, new_data = vm.get_efi_var('db', guid)

        assert old_attrs == new_attrs
        assert len(old_data) < len(new_data)
        assert old_data in new_data
    finally:
        if vm.is_running():
            vm.shutdown()
        vm.host.ssh(['varstore-sb-state', vm.uuid, 'setup'])
