import logging

from lib.common import wait_for
from lib.efi import EFIAuth

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
