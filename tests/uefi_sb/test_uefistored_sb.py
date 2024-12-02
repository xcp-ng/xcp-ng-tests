import logging
import pytest

from lib.commands import SSHCommandFailed
from lib.common import wait_for

from .utils import _test_key_exchanges, boot_and_check_no_sb_errors, boot_and_check_sb_failed, \
    boot_and_check_sb_succeeded, generate_keys, revert_vm_state, sign_efi_bins, VM_SECURE_BOOT_FAILED, \
    _test_uefi_var_lifecycle

# These tests check the behaviour of XAPI and uefistored as they are in XCP-ng 8.2
# For XCP-ng 8.3 or later, see test_varstored_sb.py

# Requirements:
# On the test runner:
# - See requirements documented in the project's README.md for Guest UEFI Secure Boot tests
# From --hosts parameter:
# - host: XCP-ng host 8.2.x only (+ updates)
#   with UEFI certs either absent, or present and consistent (state will be saved and restored)
# From --vm parameter
# - A UEFI VM to import
#   Some tests are Linux-only and some tests are Windows-only.

pytestmark = pytest.mark.default_vm('mini-linux-x86_64-uefi')

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_less_than_8_3")
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

    @pytest.mark.multi_vms # test that SB works on various UEFI unix/linux VMs, not just on `small_vm`
    def test_boot_success_when_pool_db_set_and_images_signed(self, uefi_vm):
        vm = uefi_vm
        vm.host.pool.install_custom_uefi_certs([self.PK, self.KEK, self.db])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_succeeded(vm)

    def test_boot_success_when_vm_db_set_and_images_signed(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_succeeded(vm)

    def test_boot_fails_when_pool_db_set_and_images_unsigned(self, uefi_vm):
        vm = uefi_vm
        vm.host.pool.install_custom_uefi_certs([self.PK, self.KEK, self.db])
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_failed(vm)

    def test_boot_fails_when_vm_db_set_and_images_unsigned(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db])
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_failed(vm)

    def test_boot_succeeds_when_pool_certs_set_and_sb_disabled(self, uefi_vm):
        vm = uefi_vm
        vm.host.pool.install_custom_uefi_certs([self.PK, self.KEK, self.db])
        vm.param_set('platform', False, key='secureboot')
        boot_and_check_no_sb_errors(vm)

    def test_boot_succeeds_when_vm_certs_set_and_sb_disabled(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db])
        vm.param_set('platform', False, key='secureboot')
        boot_and_check_no_sb_errors(vm)

    def test_boot_fails_when_pool_dbx_revokes_signed_images(self, uefi_vm):
        vm = uefi_vm
        vm.host.pool.install_custom_uefi_certs([self.PK, self.KEK, self.db, self.dbx])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_failed(vm)

    def test_boot_fails_when_vm_dbx_revokes_signed_images(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db, self.dbx])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_failed(vm)

    def test_boot_success_when_initial_pool_keys_not_signed_by_parent(self, uefi_vm):
        vm = uefi_vm
        PK, KEK, db, _ = generate_keys(self_signed=True)
        vm.host.pool.install_custom_uefi_certs([PK, KEK, db])
        sign_efi_bins(vm, db)
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_succeeded(vm)

    def test_boot_success_when_initial_vm_keys_not_signed_by_parent(self, uefi_vm):
        vm = uefi_vm
        PK, KEK, db, _ = generate_keys(self_signed=True)
        vm.install_uefi_certs([PK, KEK, db])
        sign_efi_bins(vm, db)
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_succeeded(vm)

    def test_sb_off_really_means_off(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', False, key='secureboot')
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        logging.info("Check that SB is NOT enabled according to the OS.")
        assert not vm.booted_with_secureboot()


@pytest.mark.usefixtures("host_less_than_8_3")
@pytest.mark.usefixtures("pool_without_uefi_certs", "windows_vm")
class TestGuestWindowsUEFISecureBoot:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        revert_vm_state(vm, snapshot)
        # clear pool certs for next test
        vm.host.pool.clear_uefi_certs()

    @pytest.mark.small_vm # test on the smallest Windows VM, if that means anything with Windows
    def test_windows_fails(self, uefi_vm):
        vm = uefi_vm
        PK, KEK, db, _ = generate_keys(self_signed=True)
        vm.host.pool.install_custom_uefi_certs([PK, KEK, db])
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_failed(vm)

    @pytest.mark.multi_vms # test that SB works on every Windows VM we have
    def test_windows_succeeds(self, uefi_vm):
        vm = uefi_vm
        vm.param_set('platform', True, key='secureboot')
        # Install default certs. This requires internet access from the host.
        logging.info("Install default certs on pool with secureboot-certs install")
        vm.host.ssh(['secureboot-certs', 'install'])
        boot_and_check_sb_succeeded(vm)


@pytest.mark.small_vm
@pytest.mark.usefixtures("host_less_than_8_3")
@pytest.mark.usefixtures("pool_without_uefi_certs")
class TestCertsMissingAndSbOn:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        vm.param_set('platform', True, key='secureboot')
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

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_less_than_8_3")
@pytest.mark.usefixtures("pool_without_uefi_certs", "unix_vm")
class TestUEFIKeyExchange:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        revert_vm_state(vm, snapshot)

    def test_key_exchanges(self, uefi_vm):
        vm = uefi_vm

        _test_key_exchanges(vm)

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_less_than_8_3", "vm_on_shared_sr")
@pytest.mark.usefixtures("pool_without_uefi_certs")
class TestUEFIVarMigrate:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        revert_vm_state(vm, snapshot)

    def test_uefi_var_migrate(self, host, hostA2, uefi_vm):
        _test_uefi_var_lifecycle(uefi_vm, host, hostA2)
