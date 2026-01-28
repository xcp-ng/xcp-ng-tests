from __future__ import annotations

import pytest

import logging

from lib import config
from lib.common import wait_for
from lib.vdi import VDI

from typing import Generator

@pytest.fixture(scope='module')
def vm_with_cbt(host, unix_vm) -> Generator:
    vm = unix_vm
    vm.shutdown(verify=True)

    vdi_uuids = vm.vdi_uuids()
    if not vdi_uuids:
        raise Exception(f"No VDIs found for VM {vm.uuid}")

    vdi = VDI(vdi_uuids[0], host=vm.host)
    vdi.enable_cbt()

    yield vm

    logging.info("<< Disable CBT and destroy VM")
    vm.shutdown()
    vdi.param_set('cbt-enabled', 'false')


@pytest.fixture(scope='function')
def running_vm_with_cbt(vm_with_cbt):
    vm = vm_with_cbt
    if not vm.is_running():
        vm.start()
        vm.wait_for_os_booted()

    yield vm

    if vm.is_running():
        vm.shutdown(verify=True)


def iscsi_device_config():
    return config.LVMOISCSI_DEVICE_CONFIG


@pytest.fixture(scope='package')
def iscsi_sr(host) -> Generator:
    device_config = iscsi_device_config()
    sr = host.sr_create('lvmoiscsi', 'LVMOISCSI-SR-test', device_config, verify=True)

    yield sr

    logging.info("<< Destroy iSCSI SR")
    sr.destroy(verify=True)


@pytest.fixture(scope='module')
def vm_on_iscsi_sr(host, iscsi_sr, vm_ref) -> Generator:
    vm = host.import_vm(vm_ref, sr_uuid=iscsi_sr.uuid)

    yield vm

    logging.info("<< Destroy VM on iSCSI")
    vm.destroy(verify=True)


@pytest.fixture(scope='module')
def vdi_on_iscsi_sr(iscsi_sr) -> Generator:
    vdi = iscsi_sr.create_vdi('CBT-test-VDI')

    yield vdi

    logging.info("<< Destroy VDI")
    vdi.destroy()


def get_vdi_from_vm(vm):
    vdi_uuids = vm.vdi_uuids()
    if not vdi_uuids:
        raise Exception(f"No VDIs found for VM {vm.uuid}")
    return VDI(vdi_uuids[0], host=vm.host)


def enable_cbt(vdi):
    vdi.enable_cbt()


def disable_cbt(vdi):
    vdi.disable_cbt()


def get_cbt_enabled(vdi):
    return vdi.param_get('cbt-enabled')


def list_changed_blocks(vdi, vdi_from_uuid):
    result = vdi.sr.pool.master.xe('vdi-list-changed-blocks', {
        'vdi-to-uuid': vdi.uuid,
        'vdi-from-uuid': vdi_from_uuid
    })
    return result


def enable_cbt_on_vdi(vdi):
    enable_cbt(vdi)
    wait_for(lambda: get_cbt_enabled(vdi) == 'true',
             msg=f"Waiting for CBT to be enabled on {vdi.uuid}")


def disable_cbt_on_vdi(vdi):
    disable_cbt(vdi)
    wait_for(lambda: get_cbt_enabled(vdi) == 'false',
             msg=f"Waiting for CBT to be disabled on {vdi.uuid}")


def verify_cbt_log_files_exist(vdi):
    host = vdi.sr.pool.master
    result = host.ssh(['ls', '-la', f'/var/run/nonpersistent/dp-{vdi.uuid}.cbtlog'])
    return result.returncode == 0
