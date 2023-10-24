import hashlib
import logging
import pytest

from lib.efi import get_secure_boot_guid, esl_from_auth_file

from .utils import generate_keys, revert_vm_state

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

def check_disk_cert_md5sum(host, key, reference_file):
    auth_filepath_on_host = f'{host.varstore_dir()}/{key}.auth'
    assert host.file_exists(auth_filepath_on_host)
    with open(reference_file, 'rb') as rf:
        reference_md5 = hashlib.md5(rf.read()).hexdigest()
    host_disk_md5 = host.ssh([f'md5sum {auth_filepath_on_host} | cut -d " " -f 1'])
    logging.debug('Reference MD5: %s' % reference_md5)
    logging.debug('Host disk MD5: %s' % host_disk_md5)
    assert host_disk_md5 == reference_md5

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

@pytest.mark.usefixtures("host_at_least_8_3", "pool_without_uefi_certs")
class TestPoolToDiskCertInheritanceAtXapiStart:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, host):
        yield
        host.pool.clear_uefi_certs()

    def test_pool_certs_present_and_disk_certs_absent(self, host):
        # start with certs on pool and no certs on host disks
        pool_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        # Make sure certs are synced to disk
        host.restart_toolstack(verify=True)
        logging.info('Check that the certs have been written on the disk of the host.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_disk_cert_md5sum(host, key, pool_auths[key].auth)

    def test_pool_certs_present_and_disk_certs_different(self, host):
        # start with different certs on pool and disks
        pool_auths = generate_keys(as_dict=True)
        disk_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        logging.info("Installing different certs to hosts disks")
        install_certs_to_disks(host.pool, disk_auths, ['PK', 'KEK', 'db', 'dbx'])
        # Make sure certs are synced to disk
        host.restart_toolstack(verify=True)
        logging.info('Check that the certs have been updated on the disk of the host.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_disk_cert_md5sum(host, key, pool_auths[key].auth)

    # FIXME: this behaviour will never exist in 8.3: no certs will mean "use the default certs"
    @pytest.mark.usefixtures("xfail_on_xcpng_8_3")
    def test_pool_certs_absent_and_disk_certs_present(self, host):
        # start with no pool certs and with certs on disks
        disk_auths = generate_keys(as_dict=True)
        logging.info("Installing certs to hosts disks")
        install_certs_to_disks(host.pool, disk_auths, ['PK', 'KEK', 'db', 'dbx'])
        host.restart_toolstack(verify=True)
        logging.info('Check that the certs on disk have been erased since there is none in the pool.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            assert not host.file_exists(f'{host.varstore_dir()}/{key}.auth')

    def test_pool_certs_present_and_some_different_disk_certs_present(self, host):
        # start with all certs on pool and just two certs on disks
        pool_auths = generate_keys(as_dict=True)
        disk_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        logging.info("Installing different certs to hosts disks")
        install_certs_to_disks(host.pool, disk_auths, ['KEK', 'dbx'])
        # Make sure certs are synced to disk
        host.restart_toolstack(verify=True)
        logging.info('Check that the certs have been added or updated on the disk of the host.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_disk_cert_md5sum(host, key, pool_auths[key].auth)

    @pytest.mark.usefixtures("xfail_on_xcpng_8_3")
    def test_pool_certs_present_except_dbx_and_disk_certs_different(self, host):
        # start with no dbx on pool and all, different, certs on disks
        pool_auths = generate_keys(as_dict=True)
        disk_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db']])
        logging.info("Installing different certs to hosts disks, including a dbx")
        install_certs_to_disks(host.pool, disk_auths, ['PK', 'KEK', 'db', 'dbx'])
        # Make sure certs are synced to disk
        host.restart_toolstack(verify=True)
        logging.info("Check host disk certs are in sync with pool's ones")
        for key in ['PK', 'KEK', 'db']:
            check_disk_cert_md5sum(host, key, pool_auths[key].auth)

        assert not host.file_exists(f'{host.varstore_dir()}/dbx.auth')

    def test_pool_certs_present_and_disk_certs_present_and_same(self, host):
        # start with certs on pool and no certs on host disks
        pool_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        install_certs_to_disks(host.pool, pool_auths, ['PK', 'KEK', 'db', 'dbx'])
        # Make sure certs are synced to disk
        host.restart_toolstack(verify=True)
        logging.info('Check that the certs have been written on the disk of the host.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_disk_cert_md5sum(host, key, pool_auths[key].auth)

@pytest.mark.small_vm
@pytest.mark.usefixtures("pool_without_uefi_certs")
class TestPoolToVMCertInheritance:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        # Revert the VM, which has the interesting effect of also shutting it down instantly
        revert_vm_state(vm, snapshot)
        # clear pool certs for next test
        vm.host.pool.clear_uefi_certs()

    def is_vm_cert_present(self, vm, key):
        res = vm.host.ssh(['varstore-get', vm.uuid, get_secure_boot_guid(key).as_str(), key],
                          check=False, simple_output=False, decode=False)
        return res.returncode == 0

    def get_md5sum_from_auth(self, auth):
        return hashlib.md5(esl_from_auth_file(auth)).hexdigest()

    def check_vm_cert_md5sum(self, vm, key, reference_file):
        res = vm.host.ssh(['varstore-get', vm.uuid, get_secure_boot_guid(key).as_str(), key],
                          check=False, simple_output=False, decode=False)
        assert res.returncode == 0, f"Cert {key} must be present"
        reference_md5 = self.get_md5sum_from_auth(reference_file)
        assert hashlib.md5(res.stdout).hexdigest() == reference_md5

    def test_pool_certs_absent_and_vm_certs_absent(self, uefi_vm):
        vm = uefi_vm
        # start with no certs on pool and no certs in the VM
        # start the VM so that certs may be synced to it if appropriate
        vm.start()
        logging.info("Check that the VM still has no certs")
        for key in ['PK', 'KEK', 'db', 'dbx']:
            assert not self.is_vm_cert_present(vm, key)

    def test_pool_certs_present_and_vm_certs_absent(self, uefi_vm):
        vm = uefi_vm
        # start with certs on pool and no certs in the VM
        pool_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        # start the VM so that certs may be synced to it if appropriate
        vm.start()
        logging.info("Check that the VM got the pool certs")
        for key in ['PK', 'KEK', 'db', 'dbx']:
            self.check_vm_cert_md5sum(vm, key, pool_auths[key].auth)

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
            self.check_vm_cert_md5sum(vm, key, vm_auths[key].auth)

    def test_pools_certs_absent_and_vm_certs_present(self, uefi_vm):
        vm = uefi_vm
        # start with no certs on pool and all certs in the VM
        vm_auths = generate_keys(as_dict=True)
        vm.install_uefi_certs([vm_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        # start the VM so that certs may be synced to it if appropriate
        vm.start()
        logging.info("Check that the VM certs are unchanged")
        for key in ['PK', 'KEK', 'db', 'dbx']:
            self.check_vm_cert_md5sum(vm, key, vm_auths[key].auth)

    @pytest.mark.usefixtures("host_less_than_8_3")
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
            self.check_vm_cert_md5sum(vm, key, pool_auths[key].auth)
        for key in ['db', 'dbx']:
            self.check_vm_cert_md5sum(vm, key, vm_auths[key].auth)

@pytest.mark.usefixtures("host_at_least_8_3", "hostA2", "pool_without_uefi_certs")
class TestPoolToDiskCertPropagationToAllHosts:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, host):
        yield
        host.pool.clear_uefi_certs()

    def test_set_pool_certificates(self, host):
        keys = ['PK', 'KEK', 'db', 'dbx']
        pool_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in keys])
        for h in host.pool.hosts:
            logging.info(f"Check Pool.set_uefi_certificates update host {h} certificate on disk.")
            for key in keys:
                check_disk_cert_md5sum(h, key, pool_auths[key].auth)

    def test_set_pool_certificates_partial(self, host):
        keys = ['PK', 'KEK', 'db']
        missing_key = 'dbx'
        pool_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in keys])
        for h in host.pool.hosts:
            logging.info(f"Check Pool.set_uefi_certificates update host {h} certificate on disk.")
            for key in keys:
                check_disk_cert_md5sum(h, key, pool_auths[key].auth)
            assert not h.file_exists(f'{host.varstore_dir()}/{missing_key}.auth')

    def test_clear_certificates_from_pool(self, host):
        keys = ['PK', 'KEK', 'db', 'dbx']
        pool_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in keys])
        host.pool.clear_uefi_certs()
        for h in host.pool.hosts:
            logging.info(f"Check host {h} has no certificate on disk.")
            for key in keys:
                assert not h.file_exists(f'{host.varstore_dir()}/{key}.auth')

@pytest.mark.usefixtures("host_at_least_8_3", "pool_without_uefi_certs")
class TestPoolToDiskCertInheritanceOnPoolJoin:
    @pytest.fixture(scope='function')
    def keys_auths_for_joined_host(self, host, hostB1):
        from packaging import version
        version_str = "8.3"
        if not hostB1.xcp_version >= version.parse(version_str):
            pytest.skip(f"This test requires a second XCP-ng pool with version >= {version_str}")

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
        host.pool.clear_uefi_certs()

    def test_host_certificates_updated_after_join(self, keys_auths_for_joined_host):
        keys, pool_auths, joined_host = keys_auths_for_joined_host
        for key in keys:
            check_disk_cert_md5sum(joined_host, key, pool_auths[key].auth)
