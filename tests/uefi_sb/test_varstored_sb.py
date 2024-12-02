import logging
import pytest

from .utils import _test_key_exchanges, boot_and_check_no_sb_errors, boot_and_check_sb_failed, \
    boot_and_check_sb_succeeded, generate_keys, revert_vm_state, sign_efi_bins, _test_uefi_var_lifecycle

# These tests check the behaviour of XAPI and varstored as they are in XCP-ng 8.3
# For XCP-ng 8.2, see test_uefistored_sb.py

# Requirements:
# On the test runner:
# - See requirements documented in the project's README.md for Guest UEFI Secure Boot tests
# From --hosts parameter:
# - host: XCP-ng host >= 8.3
# From --vm parameter
# - A UEFI VM to import
#   Some tests are Linux-only and some tests are Windows-only.

pytestmark = pytest.mark.default_vm('mini-linux-x86_64-uefi')

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
@pytest.mark.usefixtures("unix_vm")
class TestGuestLinuxUEFISecureBoot:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        self.PK, self.KEK, self.db, self.dbx = generate_keys()
        yield
        revert_vm_state(vm, snapshot)

    @pytest.mark.multi_vms # test that SB works on various UEFI unix/linux VMs, not just on `small_vm`
    def test_boot_success_when_vm_db_set_and_images_signed(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_succeeded(vm)

    def test_boot_fails_when_vm_db_set_and_images_unsigned(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db])
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_failed(vm)

    def test_boot_succeeds_when_vm_certs_set_and_sb_disabled(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db])
        vm.param_set('platform', False, key='secureboot')
        boot_and_check_no_sb_errors(vm)

    def test_boot_fails_when_vm_dbx_revokes_signed_images(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db, self.dbx])
        sign_efi_bins(vm, self.db)
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_failed(vm)

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


@pytest.mark.usefixtures("host_at_least_8_3")
@pytest.mark.usefixtures("windows_vm")
class TestGuestWindowsUEFISecureBoot:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        revert_vm_state(vm, snapshot)

    @pytest.mark.small_vm # test on the smallest Windows VM, if that means anything with Windows
    def test_windows_fails(self, uefi_vm):
        vm = uefi_vm
        PK, KEK, db, _ = generate_keys(self_signed=True)
        vm.install_uefi_certs([PK, KEK, db])
        vm.param_set('platform', True, key='secureboot')
        boot_and_check_sb_failed(vm)

    @pytest.mark.multi_vms # test that SB works on every Windows VM we have
    def test_windows_succeeds(self, uefi_vm):
        vm = uefi_vm
        vm.param_set('platform', True, key='secureboot')
        # Install certs in the VM. They must be official MS certs.
        # We install them first in the pool with `secureboot-certs install`, which requires internet access
        logging.info("Install MS certs on pool with secureboot-certs install")
        vm.host.ssh(['secureboot-certs', 'install'])
        # Now install the default pool certs in the VM
        vm.set_uefi_user_mode()
        boot_and_check_sb_succeeded(vm)


@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
class TestCertsMissingAndSbOn:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        vm.param_set('platform', True, key='secureboot')
        yield
        revert_vm_state(vm, snapshot)

    def test_setup_mode_and_sb_on(self, uefi_vm):
        vm = uefi_vm
        vm.set_uefi_setup_mode()
        boot_and_check_no_sb_errors(vm)

    def test_only_pk_present_but_sb_on(self, uefi_vm):
        vm = uefi_vm
        PK, _, _, _ = generate_keys()
        vm.install_uefi_certs([PK])
        boot_and_check_sb_failed(vm)

    def test_only_pk_and_kek_present_but_sb_on(self, uefi_vm):
        vm = uefi_vm
        PK, KEK, _, _ = generate_keys()
        vm.install_uefi_certs([PK, KEK])
        boot_and_check_sb_failed

    def test_only_pk_and_db_present_but_sb_on(self, uefi_vm):
        vm = uefi_vm
        PK, _, db, _ = generate_keys()
        vm.install_uefi_certs([PK, db])
        boot_and_check_sb_succeeded

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
@pytest.mark.usefixtures("unix_vm")
class TestUEFIKeyExchange:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        revert_vm_state(vm, snapshot)

    def test_key_exchanges(self, uefi_vm):
        vm = uefi_vm
        vm.set_uefi_setup_mode()

        _test_key_exchanges(vm)

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3", "vm_on_shared_sr")
class TestUEFIVarMigrate:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        revert_vm_state(vm, snapshot)

    def test_uefi_var_migrate(self, host, hostA2, uefi_vm):
        _test_uefi_var_lifecycle(uefi_vm, host, hostA2)
