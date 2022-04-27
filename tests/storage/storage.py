from lib.commands import SSHCommandFailed
from lib.common import wait_for

def try_to_create_sr_with_missing_device(sr_type, label, host):
    try:
        sr = host.sr_create(sr_type, label, {}, verify=True)
    except SSHCommandFailed as e:
        assert e.stdout == (
            'Error code: SR_BACKEND_FAILURE_90\nError parameters: , '
            + 'The request is missing the device parameter,'
        ), 'Bad error, current: {}'.format(e.stdout)
        return
    assert False, 'SR creation should not have succeeded!'

def cold_migration_then_come_back(vm, prov_host, prov_sr, dest_host, dest_sr):
    """ Storage migration of a shutdown VM, then migrate it back. """
    assert vm.is_halted()
    # Move the VM to another host of the pool
    vm.migrate(dest_host, dest_sr)
    wait_for(lambda: vm.all_vdis_on_host(dest_host), "Wait for all VDIs on destination host")
    # Start VM to make sure it works
    vm.start()
    vm.wait_for_os_booted()
    vm.shutdown(verify=True)
    # Migrate it back to the provenance SR
    vm.migrate(prov_host, prov_sr)
    wait_for(lambda: vm.all_vdis_on_host(prov_host), "Wait for all VDIs back on provenance host")
    # Start VM to make sure it works
    vm.start()
    vm.wait_for_os_booted()
    vm.shutdown(verify=True)

def live_storage_migration_then_come_back(vm, prov_host, prov_sr, dest_host, dest_sr):
    # start VM
    vm.start()
    vm.wait_for_os_booted()
    # Move the VM to another host of the pool
    vm.migrate(dest_host, dest_sr)
    wait_for(lambda: vm.all_vdis_on_host(dest_host), "Wait for all VDIs on destination host")
    wait_for(lambda: vm.is_running_on_host(dest_host), "Wait for VM to be running on destination host")
    # Migrate it back to the provenance SR
    vm.migrate(prov_host, prov_sr)
    wait_for(lambda: vm.all_vdis_on_host(prov_host), "Wait for all VDIs back on provenance host")
    wait_for(lambda: vm.is_running_on_host(prov_host), "Wait for VM to be running on provenance host")
    vm.shutdown(verify=True)
