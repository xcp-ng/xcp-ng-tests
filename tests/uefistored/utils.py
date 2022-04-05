import logging

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
