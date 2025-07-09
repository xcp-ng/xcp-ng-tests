import pytest

import logging

from .utils import generate_keys, revert_vm_state

# Requirements:
# On the test runner:
# - See requirements documented in the project's README.md for Guest UEFI Secure Boot tests
# From --hosts parameter:
# - host: XCP-ng host >= 8.3
# From --vm parameter
# - A UEFI VM to import

pytestmark = pytest.mark.default_vm('mini-linux-x86_64-uefi')

@pytest.mark.usefixtures("host_at_least_8_3")
class TestPoolGuestSecureBootReadiness:
    def test_pool_ready(self, host):
        pool_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        assert host.xe("pool-get-guest-secureboot-readiness") == "ready"

    def test_pool_ready_no_dbx(self, host):
        pool_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db']])
        assert host.xe("pool-get-guest-secureboot-readiness") == "ready_no_dbx"

    def test_pool_not_ready(self, host):
        host.pool.clear_custom_uefi_certs()
        assert host.xe("pool-get-guest-secureboot-readiness") == "not_ready"

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
class TestVmSecureBootReadiness:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        self.PK, self.KEK, self.db, self.dbx = generate_keys()
        yield
        revert_vm_state(vm, snapshot)

    def test_vm_not_supported(self, uefi_vm):
        vm = uefi_vm
        vm.param_set('HVM-boot-params', 'bios', key='firmware') # Fake BIOS VM
        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "not_supported"

    def test_vm_disabled(self, uefi_vm):
        vm = uefi_vm
        vm.param_set('platform', False, key='secureboot')
        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "disabled"

    def test_vm_first_boot(self, uefi_vm):
        vm = uefi_vm
        vm.clear_uefi_variables()
        vm.param_set('platform', True, key='secureboot')
        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "first_boot"

    def test_vm_ready(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db, self.dbx])
        vm.param_set('platform', True, key='secureboot')
        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "ready"

    def test_vm_ready_no_dbx(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.db])
        vm.param_set('platform', True, key='secureboot')
        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "ready_no_dbx"

    def test_vm_setup_mode(self, uefi_vm):
        vm = uefi_vm
        vm.param_set('platform', True, key='secureboot')
        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "setup_mode"
        vm.install_uefi_certs([self.KEK, self.db, self.dbx])
        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "setup_mode"

    def test_vm_certs_incomplete_no_kek(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.db, self.dbx])
        vm.param_set('platform', True, key='secureboot')
        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "certs_incomplete"

    def test_vm_certs_incomplete_no_db(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK, self.KEK, self.dbx])
        vm.param_set('platform', True, key='secureboot')
        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "certs_incomplete"

    def test_vm_certs_incomplete_only_pk(self, uefi_vm):
        vm = uefi_vm
        vm.install_uefi_certs([self.PK])
        vm.param_set('platform', True, key='secureboot')
        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "certs_incomplete"

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
class TestVmSetUefiMode:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        self.PK, self.KEK, self.db, self.dbx = generate_keys()
        vm.install_uefi_certs([self.PK, self.KEK, self.db, self.dbx])
        vm.param_set('platform', True, key='secureboot')
        yield
        revert_vm_state(vm, snapshot)

    def test_vm_set_uefi_mode(self, uefi_vm):
        vm = uefi_vm

        # Add certs to the pool so that `xe vm-set-uefi-mode` propagates them to the VM later in the test
        pool_auths = {'PK': self.PK, 'KEK': self.KEK, 'db': self.db, 'dbx': self.dbx}
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        assert vm.host.xe("pool-get-guest-secureboot-readiness") == "ready"

        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "ready"

        vm.host.xe("vm-set-uefi-mode", {"uuid": vm.uuid, "mode": "setup"})
        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "setup_mode"

        vm.host.xe("vm-set-uefi-mode", {"uuid": vm.uuid, "mode": "user"})
        assert vm.host.xe("vm-get-secureboot-readiness", {"uuid": vm.uuid}) == "ready"
