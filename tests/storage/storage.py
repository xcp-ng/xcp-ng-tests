import logging

from lib.commands import SSHCommandFailed
from lib.common import strtobool, wait_for, wait_for_not
from lib.sr import SR
from lib.vdi import VDI

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vm import VM


def try_to_create_sr_with_missing_device(sr_type, label, host):
    try:
        host.sr_create(sr_type, label, {}, verify=True)
    except SSHCommandFailed as e:
        assert e.stdout == (
            'Error code: SR_BACKEND_FAILURE_90\nError parameters: , ' + 'The request is missing the device parameter,'
        ), 'Bad error, current: {}'.format(e.stdout)
        return
    assert False, 'SR creation should not have succeeded!'


def cold_migration_then_come_back(vm, prov_host, dest_host, dest_sr):
    """Storage migration of a shutdown VM, then migrate it back."""
    prov_sr = vm.get_sr()
    assert vm.is_halted()
    # Move the VM to another host of the pool
    vm.migrate(dest_host, dest_sr)
    wait_for(lambda: vm.all_vdis_on_sr(dest_sr), "Wait for all VDIs on destination SR")
    # Start VM to make sure it works
    vm.start(on=dest_host.uuid)
    vm.wait_for_os_booted()
    vm.shutdown(verify=True)
    # Migrate it back to the provenance SR
    vm.migrate(prov_host, prov_sr)
    wait_for(lambda: vm.all_vdis_on_sr(prov_sr), "Wait for all VDIs back on provenance SR")
    # Start VM to make sure it works
    vm.start(on=prov_host.uuid)
    vm.wait_for_os_booted()
    vm.shutdown(verify=True)


def live_storage_migration_then_come_back(vm, prov_host, dest_host, dest_sr):
    prov_sr = vm.get_sr()
    # start VM
    vm.start(on=prov_host.uuid)
    vm.wait_for_os_booted()
    # Move the VM to another host of the pool
    vm.migrate(dest_host, dest_sr)
    wait_for(lambda: vm.all_vdis_on_sr(dest_sr), "Wait for all VDIs on destination SR")
    wait_for(lambda: vm.is_running_on_host(dest_host), "Wait for VM to be running on destination host")
    # Migrate it back to the provenance SR
    vm.migrate(prov_host, prov_sr)
    wait_for(lambda: vm.all_vdis_on_sr(prov_sr), "Wait for all VDIs back on provenance SR")
    wait_for(lambda: vm.is_running_on_host(prov_host), "Wait for VM to be running on provenance host")
    vm.shutdown(verify=True)


def vdi_is_open(vdi):
    sr = vdi.sr

    get_sr_ref = f"""
import sys
import XenAPI

def get_xapi_session():
    session = XenAPI.xapi_local()
    try:
        session.xenapi.login_with_password('root', '', '', 'xcp-ng-tests session')
    except Exception as e:
        raise Exception('Cannot get XAPI session: {{}}'.format(e))
    return session

session = get_xapi_session()
sr_ref = session.xenapi.SR.get_by_uuid(\"{sr.uuid}\")
print(sr_ref)
"""

    master = sr.pool.master
    return strtobool(
        master.call_plugin(
            'on-slave', 'is_open', {'vdiUuid': vdi.uuid, 'srRef': master.execute_script(get_sr_ref, shebang='python')}
        )
    )


def operation_on_vdi(host: 'Host', vdi_uuid: str, vdi_op: Literal["snapshot", "clone"]) -> VDI:
    new_vdi = host.xe(f"vdi-{vdi_op}", {"uuid": vdi_uuid})
    logging.info(f"{vdi_op.capitalize()} VDI {vdi_uuid}: {new_vdi}")
    return VDI(new_vdi, host=host)


def wait_for_vdi_coalesce(vdi: VDI):
    # It is necessary to wait a long time because the GC can be paused for more than 5 minutes.
    # And it is also necessary to allow a sufficiently long merge time which depends on the amount of data.
    wait_for_not(lambda: vdi.get_parent(), msg="Waiting for coalesce", timeout_secs=7 * 60)
    logging.info("Coalesce done")


def install_randstream(vm: 'VM'):
    vm.ssh("wget -nv https://github.com/xcp-ng/randstream/releases/download/0.3.1/randstream-0.3.1-x86_64-unknown-linux-musl.tar.gz -O - | tar -xzC /usr/bin/ ./randstream")  # noqa: E501
