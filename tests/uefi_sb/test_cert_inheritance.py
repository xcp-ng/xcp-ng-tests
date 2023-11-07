import hashlib
import logging
import pytest

from .utils import check_disk_cert_md5sum, check_vm_cert_md5sum, generate_keys, revert_vm_state

# Requirements:
# On the test runner:
# - See requirements documented in the project's README.md for Guest UEFI Secure Boot tests
# From --hosts parameter:
# - host(A1): XCP-ng host >= 8.2 (+ updates) (or >= 8.3 for other tests)
#   with UEFI certs either absent, or present and consistent (state will be saved and restored)
#   Master of a, at least, 2 hosts pool
# - hostB1: XCP-ng host >= 8.3 (required only if hostA1 is already >=8.3, else no hostB1 is needed)
#   This host will be joined and ejected from pool A, it means its state will be completely reinitialized from scratch

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


@pytest.mark.usefixtures("host_at_least_8_3", "hostA2")
class TestPoolToDiskCertPropagationToAllHosts:
    def test_set_pool_certificates(self, host):
        keys = ['PK', 'KEK', 'db', 'dbx']
        pool_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in keys])
        for h in host.pool.hosts:
            logging.info(f"Check Pool.set_uefi_certificates updated host {h} certificates in {host.varstore_dir()}.")
            assert not h.is_symlink(host.varstore_dir())
            for key in keys:
                check_disk_cert_md5sum(h, key, pool_auths[key].auth)

    def test_set_pool_certificates_partial(self, host):
        keys = ['PK', 'KEK', 'db']
        missing_key = 'dbx'
        pool_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in keys])
        for h in host.pool.hosts:
            logging.info(f"Check Pool.set_uefi_certificates updated host {h} certificates in {host.varstore_dir()}.")
            assert not h.is_symlink(host.varstore_dir())
            for key in keys:
                check_disk_cert_md5sum(h, key, pool_auths[key].auth)
            assert not h.file_exists(f'{host.varstore_dir()}/{missing_key}.auth')

    # FIXME
    @pytest.mark.xfail(reason="certificate clear doesn't restore the symlink at the moment")
    def test_clear_custom_pool_certificates(self, host):
        keys = ['PK', 'KEK', 'db', 'dbx']
        pool_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in keys])
        host.pool.clear_custom_uefi_certs()
        for h in host.pool.hosts:
            logging.info(f"Check host {h} has no custom certificates on disk.")
            assert h.is_symlink(host.varstore_dir())

@pytest.mark.usefixtures("host_at_least_8_3")
class TestPoolToDiskCertInheritanceOnPoolJoin:
    @pytest.fixture(scope='function')
    def keys_auths_for_joined_host(self, host, hostB1):
        from packaging import version
        version_str = "8.3"
        if not hostB1.xcp_version >= version.parse(version_str):
            raise Exception(f"This test requires a second XCP-ng pool with version >= {version_str}")

        # Install certs before host join
        keys = ['PK', 'KEK', 'db', 'dbx']
        pool_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in keys])

        logging.info(f"> Join host {hostB1} to pool {host} after certificates installed.")
        hostB1.join_pool(host.pool)
        joined_host = host.pool.get_host_by_uuid(hostB1.uuid)
        yield keys, pool_auths, joined_host

        logging.info(f"< Eject host {joined_host} from pool {host}.")
        # Warning: triggers a reboot of ejected host.
        host.pool.eject_host(joined_host)
        host.pool.clear_custom_uefi_certs()

    def test_host_certificates_updated_after_join(self, keys_auths_for_joined_host):
        keys, pool_auths, joined_host = keys_auths_for_joined_host
        for key in keys:
            check_disk_cert_md5sum(joined_host, key, pool_auths[key].auth)
