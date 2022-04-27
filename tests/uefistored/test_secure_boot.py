import logging
import pytest

from lib.commands import SSHCommandFailed
from lib.common import wait_for
from lib.efi import EFIAuth, EFI_AT_ATTRS_BYTES

from .utils import generate_keys, revert_vm_state, VM_SECURE_BOOT_FAILED

# Requirements:
# On the test runner:
# - See requirements documented in the project's README.md for Guest UEFI Secure Boot tests
# From --hosts parameter:
# - host: XCP-ng host >= 8.2 (+ updates)
#   with UEFI certs either absent, or present and consistent (state will be saved and restored)
# From --vm parameter
# - A UEFI VM to import
#   Some tests are Linux-only and some tests are Windows-only.

pytestmark = pytest.mark.default_vm('mini-linux-x86_64-uefi')

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

@pytest.mark.usefixtures("pool_without_uefi_certs", "unix_vm")
class TestGuestLinuxUEFISecureBoot:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        self.PK, self.KEK, self.db, self.dbx = generate_keys()
        yield
        revert_vm_state(vm, snapshot)
        # clear pool certs for next test
        vm.host.pool.clear_uefi_certs()

    def test_boot_succeeds_when_pool_certs_set_and_sb_disabled(self, uefi_vm):
        vm = uefi_vm
        vm.host.pool.install_custom_uefi_certs([self.PK, self.KEK, self.db])
        vm.param_set('platform', 'secureboot', False)
        boot_and_check_no_sb_errors(vm)

    def test_boot_succeeds_when_vm_certs_set_and_sb_disabled(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db])
        vm.param_set('platform', 'secureboot', False)
        boot_and_check_no_sb_errors(vm)

    def test_boot_fails_when_pool_db_set_and_images_unsigned(self, uefi_vm):
        vm = uefi_vm
        vm.host.pool.install_custom_uefi_certs([self.PK, self.KEK, self.db])
        vm.param_set('platform', 'secureboot', True)
        boot_and_check_sb_failed(vm)

    def test_boot_fails_when_vm_db_set_and_images_unsigned(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db])
        vm.param_set('platform', 'secureboot', True)
        boot_and_check_sb_failed(vm)

    def test_boot_success_when_pool_db_set_and_images_signed(self, uefi_vm):
        vm = uefi_vm
        vm.host.pool.install_custom_uefi_certs([self.PK, self.KEK, self.db])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', 'secureboot', True)
        boot_and_check_sb_succeeded(vm)

    def test_boot_success_when_vm_db_set_and_images_signed(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', 'secureboot', True)
        boot_and_check_sb_succeeded(vm)

    def test_boot_fails_when_pool_dbx_revokes_signed_images(self, uefi_vm):
        vm = uefi_vm
        vm.host.pool.install_custom_uefi_certs([self.PK, self.KEK, self.db, self.dbx])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', 'secureboot', True)
        boot_and_check_sb_failed(vm)

    def test_boot_fails_when_vm_dbx_revokes_signed_images(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db, self.dbx])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', 'secureboot', True)
        boot_and_check_sb_failed(vm)

    def test_boot_success_when_initial_pool_keys_not_signed_by_parent(self, uefi_vm):
        vm = uefi_vm
        PK, KEK, db, _ = generate_keys(self_signed=True)
        vm.host.pool.install_custom_uefi_certs([PK, KEK, db])
        sign_efi_bins(vm, db)
        vm.param_set('platform', 'secureboot', True)
        boot_and_check_sb_succeeded(vm)

    def test_boot_success_when_initial_vm_keys_not_signed_by_parent(self, uefi_vm):
        vm = uefi_vm
        PK, KEK, db, _ = generate_keys(self_signed=True)
        vm.install_uefi_certs([PK, KEK, db])
        sign_efi_bins(vm, db)
        vm.param_set('platform', 'secureboot', True)
        boot_and_check_sb_succeeded(vm)

    def test_sb_off_really_means_off(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', 'secureboot', False)
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        logging.info("Check that SB is NOT enabled according to the OS.")
        assert not vm.booted_with_secureboot()


@pytest.mark.usefixtures("pool_without_uefi_certs", "windows_vm")
class TestGuestWindowsUEFISecureBoot:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        revert_vm_state(vm, snapshot)
        # clear pool certs for next test
        vm.host.pool.clear_uefi_certs()

    def test_windows_fails(self, uefi_vm):
        vm = uefi_vm
        PK, KEK, db, _ = generate_keys(self_signed=True)
        vm.host.pool.install_custom_uefi_certs([PK, KEK, db])
        vm.param_set('platform', 'secureboot', True)
        boot_and_check_sb_failed(vm)

    def test_windows_succeeds(self, uefi_vm):
        vm = uefi_vm
        vm.param_set('platform', 'secureboot', True)
        # Install default certs. This requires internet access from the host.
        logging.info("Install default certs on pool with secureboot-certs install")
        vm.host.ssh(['secureboot-certs', 'install'])
        boot_and_check_sb_succeeded(vm)


@pytest.mark.usefixtures("pool_without_uefi_certs")
class TestCertsMissingAndSbOn:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        vm.param_set('platform', 'secureboot', True)
        yield
        revert_vm_state(vm, snapshot)
        # clear pool certs for next test
        vm.host.pool.clear_uefi_certs()

    def check_vm_start_fails_and_uefistored_dies(self, vm):
        with pytest.raises(SSHCommandFailed) as excinfo:
            vm.start()
        assert 'An emulator required to run this VM failed to start' in excinfo.value.stdout
        logging.info('Verified that uefistored killed itself to prevent the VM start')
        wait_for(
            lambda: vm.get_messages(VM_SECURE_BOOT_FAILED),
            'Wait for message %s' % VM_SECURE_BOOT_FAILED,
        )
        # Just in case it managed to start somehow, be it in UEFI shell only
        assert vm.is_halted()

    def test_no_certs_but_sb_on(self, uefi_vm):
        vm = uefi_vm
        self.check_vm_start_fails_and_uefistored_dies(vm)

    def test_only_pk_present_but_sb_on(self, uefi_vm):
        vm = uefi_vm
        PK, _, _, _ = generate_keys()
        vm.install_uefi_certs([PK])
        self.check_vm_start_fails_and_uefistored_dies(vm)

    def test_only_pk_and_kek_present_but_sb_on(self, uefi_vm):
        vm = uefi_vm
        PK, KEK, _, _ = generate_keys()
        vm.install_uefi_certs([PK, KEK])
        self.check_vm_start_fails_and_uefistored_dies(vm)

    def test_only_kek_and_db_present_but_sb_on(self, uefi_vm):
        vm = uefi_vm
        _, KEK, db, _ = generate_keys()
        vm.install_uefi_certs([KEK, db])
        self.check_vm_start_fails_and_uefistored_dies(vm)

    def test_only_pk_and_db_present_but_sb_on(self, uefi_vm):
        vm = uefi_vm
        PK, _, db, _ = generate_keys()
        vm.install_uefi_certs([PK, db])
        self.check_vm_start_fails_and_uefistored_dies(vm)

    def test_only_db_present_but_sb_on(self, uefi_vm):
        vm = uefi_vm
        _, _, db, _ = generate_keys()
        vm.install_uefi_certs([db])
        self.check_vm_start_fails_and_uefistored_dies(vm)

@pytest.mark.usefixtures("pool_without_uefi_certs", "unix_vm")
class TestUEFIKeyExchange:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        revert_vm_state(vm, snapshot)

    def test_key_exchanges(self, uefi_vm):
        vm = uefi_vm

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
            saved_exception = None
            try:
                vm.set_efi_var(auth.name, auth.guid,
                               EFI_AT_ATTRS_BYTES, auth.auth_data)
            except SSHCommandFailed:
                ok = False

            if (should_succeed and not ok) or (ok and not should_succeed):
                raise AssertionError('Failed to set {} {}'.format(i, auth.name))
