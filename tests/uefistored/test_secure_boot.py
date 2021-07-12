import logging
import os
import pytest

from lib.common import SSHCommandFailed, wait_for
from lib.efi import EFIAuth, EFI_AT_ATTRS, EFI_AT_ATTRS_BYTES, EFI_GUID_STRS

VM_SECURE_BOOT_FAILED = 'VM_SECURE_BOOT_FAILED'


def check_sb_failed(vm):
    vm.start()
    wait_for(
        lambda: vm.get_messages(VM_SECURE_BOOT_FAILED),
        'Wait for message %s' % VM_SECURE_BOOT_FAILED,
    )

    # If there is a VM_SECURE_BOOT_FAILED message and yet the OS still
    # successfully booted, this is a uefistored bug
    assert not vm.is_ssh_up(), 'The OS booted when it should have failed'


def check_sb_succeeded(vm):
    vm.start()
    vm.wait_for_vm_running_and_ssh_up()
    assert not vm.get_messages(VM_SECURE_BOOT_FAILED)


def sign_efi_bins(vm, db):
    '''Boots the VM if it is halted, signs the bootloader, and halts the
    VM again (if halted was its original state).
    '''
    shutdown = not vm.is_running()
    if shutdown:
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()

    logging.info('> Sign bootloader')
    vm.sign_efi_bins(db)

    if shutdown:
        vm.shutdown(verify=True)


def install_auths(vm, auths, use_xapi=False):
    for auth in auths:
        logging.info('> Setting {}'.format(auth.name))

        if auth.name == 'PK':
            dest = '/usr/share/uefistored/%s.auth' % auth.name
        else:
            dest = '/var/lib/uefistored/%s.auth' % auth.name

        vm.host.scp(auth.auth, dest)

        if use_xapi:
            vm.host.ssh([
                'varstore-set', vm.uuid, EFI_GUID_STRS[auth.name], auth.name,
                str(EFI_AT_ATTRS), dest,
            ])


def generate_keys(self_signed=False):
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

    return PK, KEK, db, dbx


class TestGuestLinuxUEFISecureBoot:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, imported_sb_vm):
        vm = imported_sb_vm
        if vm.is_windows:
            pytest.skip('only valid for Linux VMs')

        self.PK, self.KEK, self.db, self.dbx = generate_keys()
        vm.param_set('platform', 'secureboot', False)
        snapshot = vm.snapshot()
        yield

        try:
            snapshot.revert()
        except SSHCommandFailed:
            raise
        finally:
            # Messages may be populated from previous tests and may
            # interfere with future tests, so remove them
            logging.info('> remove guest SB messages')
            vm.rm_messages(VM_SECURE_BOOT_FAILED)

    def test_boot_succeeds_when_PK_set_and_sb_disabled(self, imported_sb_vm):
        vm = imported_sb_vm
        install_auths(vm, [self.PK])
        vm.param_set('platform', 'secureboot', False)
        check_sb_succeeded(vm)

    def test_boot_succeeds_when_PK_set_and_sb_disabled_xapi(self, imported_sb_vm):
        vm = imported_sb_vm
        install_auths(vm, [self.PK], use_xapi=True)
        vm.param_set('platform', 'secureboot', False)
        check_sb_succeeded(vm)

    def test_boot_fails_when_db_set_and_images_unsigned(self, imported_sb_vm):
        vm = imported_sb_vm
        install_auths(vm, [self.PK, self.KEK, self.db])
        vm.param_set('platform', 'secureboot', True)
        check_sb_failed(vm)

    def test_boot_fails_when_db_set_and_images_unsigned_xapi(self, imported_sb_vm):
        vm = imported_sb_vm
        install_auths(vm, [self.PK, self.KEK, self.db], use_xapi=True)
        vm.param_set('platform', 'secureboot', True)
        check_sb_failed(vm)

    def test_boot_success_when_launching_db_signed_images(self, imported_sb_vm):
        vm = imported_sb_vm
        install_auths(vm, [self.PK, self.KEK, self.db])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', 'secureboot', True)
        check_sb_succeeded(vm)

    def test_boot_success_when_launching_db_signed_images_xapi(self, imported_sb_vm):
        vm = imported_sb_vm
        install_auths(vm, [self.PK, self.KEK, self.db], use_xapi=True)
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', 'secureboot', True)
        check_sb_succeeded(vm)

    def test_boot_fails_when_launching_dbx_signed_images(self, imported_sb_vm):
        vm = imported_sb_vm
        install_auths(vm, [self.PK, self.KEK, self.db, self.dbx])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', 'secureboot', True)
        check_sb_failed(vm)

    def test_boot_fails_when_launching_dbx_signed_images_xapi(self, imported_sb_vm):
        vm = imported_sb_vm
        install_auths(vm, [self.PK, self.KEK, self.db, self.dbx], use_xapi=True)
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', 'secureboot', True)
        check_sb_failed(vm)

    def test_boot_success_when_initial_keys_not_signed_by_parent(self, imported_sb_vm):
        vm = imported_sb_vm
        PK, KEK, db, _ = generate_keys(self_signed=True)
        install_auths(vm, [PK, KEK, db])
        sign_efi_bins(vm, db)
        vm.param_set('platform', 'secureboot', True)
        check_sb_succeeded(vm)

    def test_boot_success_when_initial_keys_not_signed_by_parent_xapi(self, imported_sb_vm):
        vm = imported_sb_vm
        PK, KEK, db, _ = generate_keys(self_signed=True)
        install_auths(vm, [PK, KEK, db], use_xapi=True)
        sign_efi_bins(vm, db)
        vm.param_set('platform', 'secureboot', True)
        check_sb_succeeded(vm)


class TestGuestWindowsUEFISecureBoot:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, imported_sb_vm):
        vm = imported_sb_vm
        if not vm.is_windows:
            pytest.skip('only valid for Windows VMs')

        self.PK, self.KEK, self.db, self.dbx = generate_keys()
        vm.param_set('platform', 'secureboot', False)
        snapshot = vm.snapshot()
        yield

        try:
            snapshot.revert()
        except SSHCommandFailed:
            raise
        finally:
            # Messages may be populated from previous tests and may
            # interfere with future tests, so remove them
            logging.info('> remove guest SB messages')
            vm.rm_messages(VM_SECURE_BOOT_FAILED)

    def test_windows_fails(self, imported_sb_vm):
        vm = imported_sb_vm
        PK, KEK, db, _ = generate_keys(self_signed=True)
        install_auths(vm, [PK, KEK, db])
        vm.param_set('platform', 'secureboot', True)
        check_sb_failed(vm)

    def test_windows_succeeds(self, imported_sb_vm):
        vm = imported_sb_vm
        PK, _, _, _ = generate_keys(self_signed=True)
        install_auths(vm, [PK])
        vm.param_set('platform', 'secureboot', True)
        vm.host.ssh(['secureboot-certs'])
        check_sb_succeeded(vm)

        # Cleanup secureboot-certs certs
        vm.host.ssh(['rm', '/var/lib/uefistored/db.auth'])
        vm.host.ssh(['rm', '/var/lib/uefistored/KEK.auth'])


class TestUEFIKeyExchange:
    def test_key_exchanges(self, imported_sb_vm):
        vm = imported_sb_vm
        if vm.is_windows:
            pytest.skip('only valid for Linux VMs')

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

        if vm.is_running():
            vm.shutdown(force=True, verify=True)

        # Clear all SB keys
        vm.host.ssh(['varstore-sb-state', vm.uuid, 'setup'])

        # Start with only PK, we will test adding the rest of the keys
        vm.host.ssh(['rm', '/usr/share/uefistored/*'], check=False)
        vm.host.scp(PK.auth, '/usr/share/uefistored/PK.auth')
        vm.param_set('platform', 'secureboot', False)

        if not vm.is_running():
            vm.start()
            vm.wait_for_vm_running_and_ssh_up()

        tests = [
            # Clear the PK
            (null_PK, True),
            # Set the PK
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
                vm.set_efi_var(auth.name, EFI_GUID_STRS[auth.name],
                               EFI_AT_ATTRS_BYTES, auth.auth_data)
            except SSHCommandFailed:
                ok = False

            if (should_succeed and not ok) or (ok and not should_succeed):
                raise AssertionError('Failed to set {} {}'.format(i, auth.name))
