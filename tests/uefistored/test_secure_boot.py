import hashlib
import logging
import pytest

from lib.commands import SSHCommandFailed
from lib.common import wait_for
from lib.efi import EFIAuth, EFI_AT_ATTRS_BYTES, get_secure_boot_guid, esl_from_auth_file

VM_SECURE_BOOT_FAILED = 'VM_SECURE_BOOT_FAILED'

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


@pytest.mark.usefixtures("pool_without_uefi_certs")
class TestGuestLinuxUEFISecureBoot:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        if vm.is_windows:
            pytest.skip('only valid for Linux VMs')
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


@pytest.mark.usefixtures("pool_without_uefi_certs")
class TestGuestWindowsUEFISecureBoot:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        if not vm.is_windows:
            pytest.skip('only valid for Windows VMs')
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

@pytest.mark.usefixtures("pool_without_uefi_certs")
class TestUEFIKeyExchange:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        if vm.is_windows:
            pytest.skip('only valid for Linux VMs')
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

@pytest.mark.usefixtures("pool_without_uefi_certs")
class TestPoolToDiskCertInheritance:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        # Revert the VM, which has the interesting effect of also shutting it down instantly
        revert_vm_state(vm, snapshot)
        # clear pool certs for next test
        vm.host.pool.clear_uefi_certs()

    def install_certs_to_disks(self, pool, certs_dict, keys):
        for host in pool.hosts:
            for key in keys:
                host.scp(certs_dict[key].auth, f'/var/lib/uefistored/{key}.auth')

    def check_disk_cert_md5sum(self, host, key, reference_file):
        auth_filepath_on_host = f'/var/lib/uefistored/{key}.auth'
        assert host.file_exists(auth_filepath_on_host)
        reference_md5 = hashlib.md5(open(reference_file, 'rb').read()).hexdigest()
        host_disk_md5 = host.ssh([f'md5sum {auth_filepath_on_host} | cut -d " " -f 1'])
        assert host_disk_md5 == reference_md5

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
            self.check_disk_cert_md5sum(residence_host, key, pool_auths[key].auth)

    def test_pool_certs_present_and_disk_certs_different(self, uefi_vm):
        vm = uefi_vm
        # start with different certs on pool and disks
        pool_auths = generate_keys(as_dict=True)
        disk_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        logging.info("Installing different certs to hosts disks")
        self.install_certs_to_disks(vm.host.pool, disk_auths, ['PK', 'KEK', 'db', 'dbx'])
        # start a VM so that certs may be synced to disk if appropriate
        vm.start()
        residence_host = vm.get_residence_host()
        logging.info('Check that the certs have been updated on the disk of the host that started the VM.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            self.check_disk_cert_md5sum(residence_host, key, pool_auths[key].auth)

    def test_pool_certs_absent_and_disk_certs_present(self, uefi_vm):
        vm = uefi_vm
        # start with no pool certs and with certs on disks
        disk_auths = generate_keys(as_dict=True)
        logging.info("Installing certs to hosts disks")
        self.install_certs_to_disks(vm.host.pool, disk_auths, ['PK', 'KEK', 'db', 'dbx'])
        # start a VM so that certs may be synced to disk if appropriate
        vm.start()
        residence_host = vm.get_residence_host()
        logging.info('Check that the certs on disk have not changed after the VM started.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            self.check_disk_cert_md5sum(residence_host, key, disk_auths[key].auth)

    def test_pool_certs_present_and_some_different_disk_certs_present(self, uefi_vm):
        vm = uefi_vm
        # start with all certs on pool and just two certs on disks
        pool_auths = generate_keys(as_dict=True)
        disk_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        logging.info("Installing different certs to hosts disks")
        self.install_certs_to_disks(vm.host.pool, disk_auths, ['KEK', 'dbx'])
        # start a VM so that certs may be synced to disk if appropriate
        vm.start()
        residence_host = vm.get_residence_host()
        logging.info('Check that the certs have been added or updated on the disk of the host that started the VM.')
        for key in ['PK', 'KEK', 'db', 'dbx']:
            self.check_disk_cert_md5sum(residence_host, key, pool_auths[key].auth)

    def test_pool_certs_present_except_dbx_and_disk_certs_different(self, uefi_vm):
        vm = uefi_vm
        # start with no dbx on pool and all, different, certs on disks
        pool_auths = generate_keys(as_dict=True)
        disk_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db']])
        logging.info("Installing different certs to hosts disks, including a dbx")
        self.install_certs_to_disks(vm.host.pool, disk_auths, ['PK', 'KEK', 'db', 'dbx'])
        # start a VM so that certs may be synced to disk if appropriate
        vm.start()
        residence_host = vm.get_residence_host()
        logging.info('Check that the certs have been updated on the disk of the host that started the VM, except dbx.')
        for key in ['PK', 'KEK', 'db']:
            self.check_disk_cert_md5sum(residence_host, key, pool_auths[key].auth)
        self.check_disk_cert_md5sum(residence_host, 'dbx', disk_auths[key].auth)


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
