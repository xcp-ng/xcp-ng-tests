from __future__ import annotations

import logging

from lib.commands import SSHCommandFailed
from lib.common import GiB, strtobool, wait_for, wait_for_not
from lib.host import Host
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM

from typing import Literal

RANDSTREAM_1GIB_CHECKSUM = '65280014'

def try_to_create_sr_with_missing_device(sr_type, label, host):
    try:
        host.sr_create(sr_type, label, {}, verify=True)
    except SSHCommandFailed as e:
        assert e.stdout == (
            'Error code: SR_BACKEND_FAILURE_90\nError parameters: , '
            + 'The request is missing the device parameter,'
        ), 'Bad error, current: {}'.format(e.stdout)
        return
    assert False, 'SR creation should not have succeeded!'

def cold_migration_then_come_back(vm, prov_host, dest_host, dest_sr):
    """ Storage migration of a shutdown VM, then migrate it back. """
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

def live_storage_migration_then_come_back(vm: VM, prov_host: Host, dest_host: Host, dest_sr: SR):
    prov_sr = vm.get_sr()
    vdi_name = None
    integrity_check = not vm.is_windows
    dev = ""
    vbd = None

    if integrity_check:
        vdi = prov_sr.create_vdi(virtual_size=1 * GiB)
        vdi_name = vdi.name()
        vbd = vm.connect_vdi(vdi)

    # start VM
    vm.start(on=prov_host.uuid)
    vm.wait_for_os_booted()
    if integrity_check:
        vm.wait_for_vm_running_and_ssh_up()
        install_randstream(vm)
        assert vbd is not None
        dev = f'/dev/{vbd.param_get("device")}'
        logging.info(f"Generate {dev} content")
        vm.ssh(f"randstream generate -v {dev}")
        logging.info(f"Validate {dev}")
        vm.ssh(f"randstream validate -v --expected-checksum {RANDSTREAM_1GIB_CHECKSUM} {dev}")

    # Move the VM to another host of the pool
    vm.migrate(dest_host, dest_sr)
    wait_for(lambda: vm.all_vdis_on_sr(dest_sr), "Wait for all VDIs on destination SR")
    wait_for(lambda: vm.is_running_on_host(dest_host), "Wait for VM to be running on destination host")
    if integrity_check:
        logging.info(f"Validate {dev}")
        vm.ssh(f"randstream validate -v --expected-checksum {RANDSTREAM_1GIB_CHECKSUM} {dev}")

    # Migrate it back to the provenance SR
    vm.migrate(prov_host, prov_sr)
    wait_for(lambda: vm.all_vdis_on_sr(prov_sr), "Wait for all VDIs back on provenance SR")
    wait_for(lambda: vm.is_running_on_host(prov_host), "Wait for VM to be running on provenance host")
    if integrity_check:
        logging.info(f"Validate {dev}")
        vm.ssh(f"randstream validate -v --expected-checksum {RANDSTREAM_1GIB_CHECKSUM} {dev}")

    vm.shutdown(verify=True)

    if vdi_name is not None:
        vm.destroy_vdi_by_name(vdi_name)

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
    return strtobool(master.call_plugin('on-slave', 'is_open', {
        'vdiUuid': vdi.uuid,
        'srRef': master.execute_script(get_sr_ref, shebang='python')
    }))


def install_randstream(vm: 'VM'):
    BASE_URL = 'https://github.com/xcp-ng/randstream/releases/download'
    VERSION = '0.3.1'
    CHECKSUM = {
        'Linux': '71fb54390c590e08d40330aed2818afc49f63fac69314148c7fa5eb35ff1babb',
    }
    TARGET_TRIPLE = {
        'Linux': 'x86_64-unknown-linux-musl',
    }
    logging.debug("Installing randstream")
    if vm.is_windows:
        raise ValueError("Windows is not currently supported")
    else:
        os = vm.ssh('uname -s')
        assert os in CHECKSUM, f"{os} is not currently supported"
        tt = TARGET_TRIPLE[os]
        cs = CHECKSUM[os]
        fn = '/tmp/randstream.tgz'
        vm.ssh(f"echo '{cs}  -' > {fn}.sum && wget -nv {BASE_URL}/{VERSION}/randstream-{VERSION}-{tt}.tar.gz -O - | tee {fn} | sha256sum -c {fn}.sum && tar -xzf {fn} -C /usr/bin/ ./randstream")  # noqa: E501
        vm.ssh(f"rm -f {fn} {fn}.sum")

CoalesceOperation = Literal['snapshot', 'clone']

def coalesce_integrity(vm: VM, vdi: VDI, vdi_op: CoalesceOperation):
    vbd = vm.connect_vdi(vdi)
    dev = f'/dev/{vbd.param_get("device")}'
    new_vdi = None
    try:
        vm.ssh(f"randstream generate -v {dev}")
        # default seed is 0
        vm.ssh(f"randstream validate -v --expected-checksum 65280014 {dev}")
        match vdi_op:
            case 'clone': new_vdi = vdi.clone()
            case 'snapshot': new_vdi = vdi.snapshot()
        vm.ssh(f"randstream generate -v --seed 1 --size 128Mi {dev}")
        vm.ssh(f"randstream validate -v --expected-checksum ad2ca9af {dev}")
        new_vdi = vdi.wait_for_coalesce(new_vdi.destroy)
        vm.ssh(f"randstream validate -v --expected-checksum ad2ca9af {dev}")
    finally:
        vm.disconnect_vdi(vdi)
        if new_vdi is not None:
            new_vdi.destroy()
