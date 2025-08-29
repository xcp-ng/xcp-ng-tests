import pytest

import logging

from lib.common import wait_for

from .utils import check_disk_cert_md5sum, check_vm_cert_md5sum, generate_keys, revert_vm_state

# These tests check the behaviour of XAPI and varstored as they are in XCP-ng 8.3
# For XCP-ng 8.2, see test_uefistored_cert_flow.py

# Requirements:
# On the test runner:
# - See requirements documented in the project's README.md for Guest UEFI Secure Boot tests
# From --hosts parameter:
# - host: XCP-ng host >= 8.3
#   Master of a, at least, 2 hosts pool
#   With a free disk
# - hostB1: XCP-ng host >= 8.3
#   This host will be joined and ejected from pool A, it means its state will be completely reinitialized from scratch

pytestmark = pytest.mark.default_vm('mini-linux-x86_64-uefi')

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

    def test_clear_custom_pool_certificates(self, host):
        keys = ['PK', 'KEK', 'db', 'dbx']
        pool_auths = generate_keys(as_dict=True)
        host.pool.install_custom_uefi_certs([pool_auths[key] for key in keys])
        host.pool.clear_custom_uefi_certs()
        for h in host.pool.hosts:
            logging.info(f"Check host {h} has no custom certificates on disk.")
            assert h.is_symlink(host.varstore_dir())
            logging.info(f"Check host {h} only has PK, and no other certs.")
            assert h.ssh(['ls', '/var/lib/varstored/']) == 'PK.auth'

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
class TestVMCertMisc:
    @pytest.fixture(autouse=True, scope="function")
    def auto_revert_vm(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        # Revert the VM, which has the interesting effect of also shutting it down instantly
        revert_vm_state(vm, snapshot)

    def test_snapshot_revert_restores_certs(self, uefi_vm):
        vm = uefi_vm
        vm_auths = generate_keys(as_dict=True)
        vm.install_uefi_certs([vm_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        snapshot = vm.snapshot()
        try:
            # clear all certs
            vm.set_uefi_setup_mode()
            snapshot.revert()
            logging.info("Check that the VM certs were restored")
            for key in ['PK', 'KEK', 'db', 'dbx']:
                check_vm_cert_md5sum(vm, key, vm_auths[key].auth)
        finally:
            snapshot.destroy()

    def test_vm_import_restores_certs(self, uefi_vm, formatted_and_mounted_ext4_disk):
        vm = uefi_vm
        vm_auths = generate_keys(as_dict=True)
        vm.install_uefi_certs([vm_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        filepath = formatted_and_mounted_ext4_disk + '/test-export-with-uefi-certs.xva'
        vm.export(filepath, 'zstd')
        vm2 = None
        try:
            vm2 = vm.host.import_vm(filepath)
            logging.info("Check that the VM certs were imported with the VM")
            for key in ['PK', 'KEK', 'db', 'dbx']:
                check_vm_cert_md5sum(vm2, key, vm_auths[key].auth)
        finally:
            try:
                if vm2 is not None:
                    logging.info(f"Destroy VM {vm2.uuid}")
                    vm2.destroy(verify=True)
            finally:
                vm.host.ssh(['rm', '-f', filepath], check=False)

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
class TestPoolToVMCertInheritance:
    @pytest.fixture(autouse=True, scope="function")
    def auto_revert_vm(self, uefi_vm_and_snapshot):
        vm, snapshot = uefi_vm_and_snapshot
        yield
        # Revert the VM, which has the interesting effect of also shutting it down instantly
        revert_vm_state(vm, snapshot)

    def test_start_vm_without_uefi_vars(self, uefi_vm):
        # The only situation where varstored will propagate the certs automatically
        # at VM start is when the VM looks like it never started, that is it has no
        # UEFI vars at all in its NVRAM.
        vm = uefi_vm
        vm.clear_uefi_variables()
        pool_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        vm.start()
        logging.info("Check that the VM certs were updated: PK, KEK, db, dbx")
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_vm_cert_md5sum(vm, key, pool_auths[key].auth)

    def test_start_vm_without_uefi_vars_on_pool_with_only_pk(self, uefi_vm):
        # When a VM first starts but the pool doesn't have certs configured,
        # this used, until late in 8.3 development, to *not* propagate the certs to the VM
        # and we had no test that detected this situation.
        # We have now changed the behaviour, propagating the certs even if just PK is present.
        vm = uefi_vm
        vm.clear_uefi_variables()
        vm.host.pool.clear_custom_uefi_certs()
        vm.start()
        logging.info("Check that the VM certs were updated: PK only")
        assert vm.is_cert_present('PK')
        for key in ['KEK', 'db', 'dbx']:
            assert not vm.is_cert_present(key)

    def test_start_vm_in_setup_mode(self, uefi_vm):
        # In setup mode, no cert is set, but other UEFI variables are present.
        # varstored will *not* propagate the certs in this case.
        vm = uefi_vm
        pool_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        # start the VM so that certs may be synced to it if appropriate
        vm.start()
        logging.info("Check that the VM certs are unchanged")
        for key in ['PK', 'KEK', 'db', 'dbx']:
            assert not vm.is_cert_present(key)

    def test_start_vm_which_already_has_pk(self, uefi_vm):
        vm = uefi_vm
        pool_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        vm_auths = generate_keys(as_dict=True)
        vm.install_uefi_certs([vm_auths['PK']])
        # start the VM so that certs may be synced to it if appropriate
        vm.start()
        logging.info("Check that the VM certs are unchanged")
        check_vm_cert_md5sum(vm, 'PK', vm_auths['PK'].auth)
        for key in ['KEK', 'db', 'dbx']:
            assert not vm.is_cert_present(key)

    def test_switching_to_user_mode(self, uefi_vm):
        vm = uefi_vm
        pool_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        vm.set_uefi_user_mode()
        logging.info("Check that the VM certs were updated")
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_vm_cert_md5sum(vm, key, pool_auths[key].auth)

        # Now that the VM has had the certs added, let's see what happens
        # if we call the command to switch to user mode again.
        # But first, change the certs on disk or we won't see any changes.
        new_pool_auths = generate_keys(as_dict=True)
        vm.host.pool.install_custom_uefi_certs([new_pool_auths[key] for key in ['PK', 'KEK', 'db', 'dbx']])
        vm.set_uefi_user_mode()
        logging.info("Check that the VM certs were updated again")
        for key in ['PK', 'KEK', 'db', 'dbx']:
            check_vm_cert_md5sum(vm, key, new_pool_auths[key].auth)

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
            wait_for(
                lambda: check_disk_cert_md5sum(joined_host, key, pool_auths[key].auth, do_assert=False),
                f"Wait for new host '{key}' key to be identifical to pool '{key}' key",
                60
            )
