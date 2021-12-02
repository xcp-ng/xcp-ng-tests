import pytest

from lib.commands import SSHCommandFailed
from lib.efi import (
    Certificate,
    EFI_GLOBAL_VARIABLE_GUID,
    EFI_GLOBAL_VARIABLE_GUID_STR,
    EFI_AT_ATTRS_BYTES,
)

# Requirements:
# On the test runner:
# - See requirements documented in the project's README.md for Guest UEFI Secure Boot tests
# From --hosts parameter:
# - host: XCP-ng host >= 8.2 (+ updates)
# From --vm parameter
# - A UEFI VM to import

def set_and_assert_var(vm, cert, new, should_pass):
    var = 'myvariable'

    _, old = vm.get_efi_var(var, EFI_GLOBAL_VARIABLE_GUID_STR)

    signed = cert.sign_data(var, new, EFI_GLOBAL_VARIABLE_GUID)

    ok = True
    try:
        vm.set_efi_var(var, EFI_GLOBAL_VARIABLE_GUID_STR, EFI_AT_ATTRS_BYTES, signed)
    except SSHCommandFailed:
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

    vm.start()

    try:
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

        # Set the variable with new data, signed by a different cert
        set_and_assert_var(vm, Certificate(), b'this should fail', should_pass=False)
    finally:
        vm.shutdown(verify=True)
