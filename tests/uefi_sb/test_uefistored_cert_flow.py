import hashlib
import logging

import pytest

from .utils import check_disk_cert_md5sum, check_vm_cert_md5sum, generate_keys, revert_vm_state

# These tests check the behaviour of XAPI and uefistored as they are in XCP-ng 8.2
# For XCP-ng 8.3 or later, see test_varstored_cert_flow.py

# Requirements:
# On the test runner:
# - See requirements documented in the project's README.md for Guest UEFI Secure Boot tests
# From --hosts parameter:
# - host: XCP-ng host 8.2.x only (+ updates)
#   with UEFI certs either absent, or present and consistent (state will be saved and restored)
#   Ideally master of a pool with 2 hosts or more

pytestmark = pytest.mark.default_vm('mini-linux-x86_64-uefi')

def install_certs_to_disks(pool, certs_dict, keys):
    for host in pool.hosts:
        logging.debug('Installing to host %s:' % host.hostname_or_ip)
        for key in keys:
            value = certs_dict[key].auth
            with open(value, 'rb') as f:
                hash = hashlib.md5(f.read()).hexdigest()
            logging.debug('    - key: %s, value: %s' % (key, hash))
            host.scp(value, f'{host.varstore_dir()}/{key}.auth')

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_less_than_8_3", "pool_without_uefi_certs")
class TestPoolToDiskCertInheritanceAtVmStart:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        # Revert the VM, which has the interesting effect of also shutting it down instantly
        revert_vm_state(vm, snapshot)
        # clear pool certs for next test
        vm.host.pool.clear_uefi_certs()

    def test_pool_certs_present_and_disk_certs_absent(self, uefi_vm):
        vm = uefi_vm
        # start with certs on pool and no certs on host disks
        pool_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        # start a VM so that certs may be synced to disk if appropriate
        vm.start()
        residence_host = vm.get_residence_host()
        logging.info('Check that the certs have been written on the disk of the host that started the VM.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_disk_cert_md5sum(residence_host, key, pool_auths[key].auth)

    def test_pool_certs_present_and_disk_certs_different(self, uefi_vm):
        vm = uefi_vm
        # start with different certs on pool and disks
        pool_auths = generate_keys(as_dict=True)
        disk_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        logging.info("Installing different certs to hosts disks")
        install_certs_to_disks(vm.host.pool, disk_auths, ['PK', 'KEK', 'db', 'dbx'])
        # start a VM so that certs may be synced to disk if appropriate
        vm.start()
        residence_host = vm.get_residence_host()
        logging.info('Check that the certs have been updated on the disk of the host that started the VM.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_disk_cert_md5sum(residence_host, key, pool_auths[key].auth)

    def test_pool_certs_absent_and_disk_certs_present(self, uefi_vm):
        vm = uefi_vm
        # start with no pool certs and with certs on disks
        disk_auths = generate_keys(as_dict=True)
        logging.info("Installing certs to hosts disks")
        install_certs_to_disks(vm.host.pool, disk_auths, ['PK', 'KEK', 'db', 'dbx'])
        # start a VM so that certs may be synced to disk if appropriate
        vm.start()
        residence_host = vm.get_residence_host()
        logging.info('Check that the certs on disk have not changed after the VM started.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_disk_cert_md5sum(residence_host, key, disk_auths[key].auth)

    def test_pool_certs_present_and_some_different_disk_certs_present(self, uefi_vm):
        vm = uefi_vm
        # start with all certs on pool and just two certs on disks
        pool_auths = generate_keys(as_dict=True)
        disk_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        logging.info("Installing different certs to hosts disks")
        install_certs_to_disks(vm.host.pool, disk_auths, ['KEK', 'dbx'])
        # start a VM so that certs may be synced to disk if appropriate
        vm.start()
        residence_host = vm.get_residence_host()
        logging.info('Check that the certs have been added or updated on the disk of the host that started the VM.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_disk_cert_md5sum(residence_host, key, pool_auths[key].auth)

    def test_pool_certs_present_except_dbx_and_disk_certs_different(self, uefi_vm):
        vm = uefi_vm
        # start with no dbx on pool and all, different, certs on disks
        pool_auths = generate_keys(as_dict=True)
        disk_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db']])
        logging.info("Installing different certs to hosts disks, including a dbx")
        install_certs_to_disks(vm.host.pool, disk_auths, ['PK', 'KEK', 'db', 'dbx'])
        # start a VM so that certs may be synced to disk if appropriate
        vm.start()
        residence_host = vm.get_residence_host()
        logging.info('Check that the certs have been updated on the disk of the host that started the VM, except dbx.')
        for key in ['PK', 'KEK', 'db']:
            check_disk_cert_md5sum(residence_host, key, pool_auths[key].auth)
        check_disk_cert_md5sum(residence_host, 'dbx', disk_auths[key].auth)

    def test_pool_certs_present_and_disk_certs_present_and_same(self, uefi_vm):
        vm = uefi_vm
        # start with certs on pool and no certs on host disks
        pool_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        install_certs_to_disks(vm.host.pool, pool_auths, ['PK', 'KEK', 'db', 'dbx'])
        # start a VM so that certs may be synced to disk if appropriate
        vm.start()
        residence_host = vm.get_residence_host()
        logging.info('Check that the certs have been written on the disk of the host that started the VM.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_disk_cert_md5sum(residence_host, key, pool_auths[key].auth)


@pytest.mark.small_vm
@pytest.mark.usefixtures("host_less_than_8_3", "pool_without_uefi_certs")
class TestPoolToVMCertInheritance:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        # Revert the VM, which has the interesting effect of also shutting it down instantly
        revert_vm_state(vm, snapshot)
        # clear pool certs for next test
        vm.host.pool.clear_uefi_certs()

    def test_pool_certs_absent_and_vm_certs_absent(self, uefi_vm):
        vm = uefi_vm
        # start with no certs on pool and no certs in the VM
        # start the VM so that certs may be synced to it if appropriate
        vm.start()
        logging.info("Check that the VM still has no certs")
        for key in ['PK', 'KEK', 'db', 'dbx']:
            assert not vm.is_cert_present(key)

    def test_pool_certs_present_and_vm_certs_absent(self, uefi_vm):
        vm = uefi_vm
        # start with certs on pool and no certs in the VM
        pool_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        # start the VM so that certs may be synced to it if appropriate
        vm.start()
        logging.info("Check that the VM got the pool certs")
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_vm_cert_md5sum(vm, key, pool_auths[key].auth)

    def test_pool_certs_present_and_vm_certs_present(self, uefi_vm):
        vm = uefi_vm
        # start with all certs on pool and in the VM
        pool_auths = generate_keys(as_dict=True)
        vm_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        vm.install_uefi_certs([vm_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        # start the VM so that certs may be synced to it if appropriate
        vm.start()
        logging.info("Check that the VM certs are unchanged")
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_vm_cert_md5sum(vm, key, vm_auths[key].auth)

    def test_pools_certs_absent_and_vm_certs_present(self, uefi_vm):
        vm = uefi_vm
        # start with no certs on pool and all certs in the VM
        vm_auths = generate_keys(as_dict=True)
        vm.install_uefi_certs([vm_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        # start the VM so that certs may be synced to it if appropriate
        vm.start()
        logging.info("Check that the VM certs are unchanged")
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_vm_cert_md5sum(vm, key, vm_auths[key].auth)

    def test_pool_certs_partially_present_and_vm_certs_partially_present(self, uefi_vm):
        vm = uefi_vm
        # start with some certs on pool and some certs in the VM, partially overlaping
        pool_auths = generate_keys(as_dict=True)
        vm_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db']])
        # don't ask why the VM only has db and dbx certs. It's for the test.
        vm.install_uefi_certs([vm_auths[key] for key in ['db', 'dbx']])
        # start the VM so that certs may be synced to it if appropriate
        vm.start()
        logging.info("Check that the VM db and dbx certs are unchanged and PK and KEK were updated")
        for key in ['PK', 'KEK']:
            check_vm_cert_md5sum(vm, key, pool_auths[key].auth)
        for key in ['db', 'dbx']:
            check_vm_cert_md5sum(vm, key, vm_auths[key].auth)
