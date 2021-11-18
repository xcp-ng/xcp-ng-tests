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


def test_auth_variable(running_linux_uefi_vm):
    vm = running_linux_uefi_vm

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


def set_auth(vm, auth):
    vm.host.scp(auth.auth, '/tmp/%s.auth' % auth.name)
    vm.host.ssh([
        'varstore-set', vm.uuid, EFI_GUID_STRS[auth.name], auth.name,
        str(EFI_AT_ATTRS), '/tmp/%s.auth' % auth.name
    ])


def efivar_static(vm, name, guid, file, append=False):
    with open(file, "rb") as f:
        fpath = vm.create_file('/tmp/%s.auth' % name, f.read(), is_temp=True)

    cmd = ['-n', guid + "-" + name, '-f', fpath, '--attributes=' + hex(EFI_AT_ATTRS)]
    if append:
        cmd.append('--append')

    vm.execute_bin('tools/efivar-static', cmd)


def test_db_append(imported_vm):
    """Test append of UEFI variable db."""
    vm = imported_vm

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

    # Install normal set of certificates onto VM
    efivar_static(vm, 'db', EFI_GUID_STRS['db'], db1.auth)
    efivar_static(vm, 'KEK', EFI_GUID_STRS['KEK'], KEK.auth)
    efivar_static(vm, 'PK', EFI_GUID_STRS['PK'], PK.auth)

    old_attrs, old_data = vm.get_efi_var('db', EFI_GUID_STRS['db'])

    # Perform the append!
    efivar_static(vm, 'db', EFI_GUID_STRS['db'], db2.auth, append=True)


    # Test that the db has been appended
    new_attrs, new_data = vm.get_efi_var('db', EFI_GUID_STRS['db'])

    # Attrs should not change
    assert old_attrs == new_attrs

    # Assert that the new data has actually been append (contains old, but also new)
    assert len(old_data) < len(new_data)
    assert old_data in new_data

    vm.host.ssh(['varstore-sb-state', vm.uuid, 'setup'])
