import pytest

from lib.commands import SSHCommandFailed
from lib.efi import (
    Certificate,
    EFIAuth,
    global_variable_guid,
    EFI_AT_ATTRS_BYTES,
)

# Requirements:
# On the test runner:
# - See requirements documented in the project's README.md for Guest UEFI Secure Boot tests
# From --hosts parameter:
# - host: XCP-ng host >= 8.2 (+ updates)
# From --vm parameter
# - A Linux UEFI VM to import
# - The UEFI VM must have `efitools` installed (and `util-linux` for Alpine VM)

def set_and_assert_var(vm, cert, new, should_pass):
    var = 'myvariable'

    old = vm.get_efi_var(var, global_variable_guid)

    signed = cert.sign_data(var, new, global_variable_guid)

    ok = True
    try:
        vm.set_efi_var(var, global_variable_guid, EFI_AT_ATTRS_BYTES, signed)
    except SSHCommandFailed:
        ok = False

    ret = vm.get_efi_var(var, global_variable_guid)

    if should_pass:
        assert ret == new
    else:
        assert ret == old
        assert not ok, 'This var should not have successfully set'


@pytest.mark.usefixtures("unix_vm")
def test_auth_variable(uefi_vm):
    vm = uefi_vm
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


@pytest.mark.usefixtures("unix_vm")
def test_db_append(uefi_vm):
    """Pass if appending the DB succeeds. Otherwise, fail."""
    vm = uefi_vm

    PK, KEK, db, db2 = EFIAuth("PK"), EFIAuth("KEK"), EFIAuth("db"), Certificate("db")
    PK.sign_auth(PK)
    PK.sign_auth(KEK)
    KEK.sign_auth(db)

    vm.install_uefi_certs([PK, KEK, db])
    vm.start()
    vm.wait_for_os_booted()

    # This particular test requires a VM that has efi-updatevar
    assert vm.ssh_with_result(["which", "efi-updatevar"]).returncode == 0, "This test requires efi-updatevar"

    old = vm.get_efi_var(db.name, db.guid)

    assert old != b"", "db failed to install"

    vm_kek_key = vm.ssh(['mktemp'])
    vm.scp(KEK.cert.key, vm_kek_key)

    vm_db_cert = vm.ssh(['mktemp'])
    vm.scp(db2.pub, vm_db_cert)

    vm.ssh([
        "chattr",
        "-i",
        "/sys/firmware/efi/efivars/db-d719b2cb-3d3a-4596-a3bc-dad00e67656f"
    ])

    vm.ssh(["efi-updatevar", "-k", vm_kek_key, "-c", vm_db_cert, "-a", "db"])

    new = vm.get_efi_var(db.name, db.guid)

    vm.shutdown(verify=True)

    assert len(new) > len(old)
    assert old in new
