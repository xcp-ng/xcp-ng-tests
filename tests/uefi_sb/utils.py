import hashlib
import logging

from lib.commands import SSHCommandFailed
from lib.common import wait_for
from lib.efi import EFI_AT_ATTRS_BYTES, EFIAuth, get_md5sum_from_auth, get_secure_boot_guid

VM_SECURE_BOOT_FAILED = 'VM_SECURE_BOOT_FAILED'

def generate_keys(self_signed=False, as_dict=False):
    logging.info('Generating keys' + (' (self signed)' if self_signed else ''))
    PK = EFIAuth('PK')
    KEK = EFIAuth('KEK')
    db = EFIAuth('db')

    if self_signed:
        PK.sign_auth(PK)
        KEK.sign_auth(KEK)
        db.sign_auth(db)
    else:
        PK.sign_auth(PK)
        PK.sign_auth(KEK)
        KEK.sign_auth(db)

    # For our tests the dbx blacklists anything signed by the db
    dbx = EFIAuth.copy(db, name='dbx')

    if as_dict:
        return {
            'PK': PK,
            'KEK': KEK,
            'db': db,
            'dbx': dbx
        }
    else:
        return PK, KEK, db, dbx

def revert_vm_state(vm, snapshot):
    try:
        snapshot.revert()
    finally:
        # Messages may be populated from previous tests and may
        # interfere with future tests, so remove them
        logging.info('> remove guest SB messages')
        vm.rm_messages(VM_SECURE_BOOT_FAILED)

def boot_and_check_sb_failed(vm):
    vm.start()
    wait_for(
        lambda: vm.get_messages(VM_SECURE_BOOT_FAILED),
        'Wait for message %s' % VM_SECURE_BOOT_FAILED
    )

    # If there is a VM_SECURE_BOOT_FAILED message and yet the OS still
    # successfully booted, this is a uefistored bug
    assert vm.is_in_uefi_shell()

def boot_and_check_no_sb_errors(vm):
    vm.start()
    vm.wait_for_vm_running_and_ssh_up()
    logging.info("Verify there's no %s message" % VM_SECURE_BOOT_FAILED)
    assert not vm.get_messages(VM_SECURE_BOOT_FAILED)

def boot_and_check_sb_succeeded(vm):
    boot_and_check_no_sb_errors(vm)
    logging.info("Check that SB is enabled according to the OS.")
    assert vm.booted_with_secureboot()

def sign_efi_bins(vm, db):
    """
    Sign a unix VM's EFI binaries.

    Boots the VM if it is halted, signs the bootloader, and halts the
    VM again (if halted was its original state).
    """
    shutdown = not vm.is_running()
    if shutdown:
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()

    logging.info('> Sign bootloader')
    vm.sign_efi_bins(db)

    if shutdown:
        vm.shutdown(verify=True)

def _test_key_exchanges(vm):
    PK = EFIAuth('PK')
    null_PK = EFIAuth('PK', is_null=True)
    new_PK = EFIAuth('PK')
    bad_PK = EFIAuth('PK')

    KEK = EFIAuth('KEK')
    null_KEK = EFIAuth('KEK', is_null=True)

    db_from_KEK = EFIAuth('db')
    db_from_PK = EFIAuth('db')
    null_db_from_KEK = EFIAuth('db', is_null=True)
    null_db_from_PK = EFIAuth('db', is_null=True)

    PK.sign_auth(PK)
    PK.sign_auth(null_PK)
    PK.sign_auth(KEK)
    PK.sign_auth(null_KEK)
    PK.sign_auth(new_PK)
    PK.sign_auth(db_from_PK)
    PK.sign_auth(null_db_from_PK)
    PK.sign_auth(db_from_KEK)
    PK.sign_auth(null_db_from_KEK)
    KEK.sign_auth(db_from_KEK)
    KEK.sign_auth(null_db_from_KEK)
    bad_PK.sign_auth(bad_PK)

    vm.start()
    vm.wait_for_vm_running_and_ssh_up()

    # at this point we should have a VM with no certs, on a pool with no certs either

    tests = [
        # Set the PK
        (PK, True),
        # Clear the PK
        (null_PK, True),
        # Set the PK again
        (PK, True),
        # Set a PK with the wrong sig, should fail and PK should be unchanged
        (bad_PK, False),
        # Set, clear, and reset the KEK
        (KEK, True),
        (null_KEK, True),
        (KEK, True),
        # Set and clear the db signed by the KEK
        (db_from_KEK, True),
        (null_db_from_KEK, True),
        # Set and clear the db signed by the PK
        (db_from_PK, True),
        (null_db_from_PK, True),
        # Set a new PK
        (new_PK, True),
        # Set old PK, should fail due to expired timestamp
        (PK, False),
    ]

    for i, (auth, should_succeed) in enumerate(tests):
        logging.info('> Testing {} ({})'.format(auth.name, i))

        ok = True
        try:
            vm.set_efi_var(auth.name, auth.guid,
                           EFI_AT_ATTRS_BYTES, auth.auth_data())
        except SSHCommandFailed:
            ok = False

        if (should_succeed and not ok) or (ok and not should_succeed):
            raise AssertionError('Failed to set {} {}'.format(i, auth.name))

def check_disk_cert_md5sum(host, key, reference_file, do_assert=True):
    auth_filepath_on_host = f'{host.varstore_dir()}/{key}.auth'
    assert host.file_exists(auth_filepath_on_host)
    with open(reference_file, 'rb') as rf:
        reference_md5 = hashlib.md5(rf.read()).hexdigest()
    host_disk_md5 = host.ssh([f'md5sum {auth_filepath_on_host} | cut -d " " -f 1'])
    logging.debug('Reference MD5: %s' % reference_md5)
    logging.debug('Host disk MD5: %s' % host_disk_md5)
    if do_assert:
        assert host_disk_md5 == reference_md5
    else:
        return host_disk_md5 == reference_md5

def check_vm_cert_md5sum(vm, key, reference_file):
    res = vm.host.ssh(['varstore-get', vm.uuid, get_secure_boot_guid(key).as_str(), key],
                      check=False, simple_output=False, decode=False)
    assert res.returncode == 0, f"Cert {key} must be present"
    reference_md5 = get_md5sum_from_auth(reference_file)
    assert hashlib.md5(res.stdout).hexdigest() == reference_md5
