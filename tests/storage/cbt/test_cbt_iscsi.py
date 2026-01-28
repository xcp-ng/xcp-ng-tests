from __future__ import annotations

import pytest

from lib.common import vm_image, wait_for
from tests.storage.cbt.conftest import (
    enable_cbt,
    get_cbt_enabled,
    get_vdi_from_vm,
    iscsi_device_config,
    iscsi_sr,
    list_changed_blocks,
    vm_on_iscsi_sr,
)

class TestISCSISRWithCBTCreateDestroy:

    def test_create_and_destroy_iscsi_sr(self, host):
        device_config = iscsi_device_config()
        sr = host.sr_create('lvmoiscsi', 'LVMOISCSI-SR-CBT-test', device_config, verify=True)
        sr.destroy(verify=True)

    @pytest.mark.quicktest
    def test_quicktest_iscsi_sr(self, iscsi_sr):
        iscsi_sr.run_quicktest()


@pytest.mark.usefixtures("iscsi_sr")
class TestISCSISRWithCBT:

    def test_enable_cbt_on_iscsi_vdi(self, host, vm_on_iscsi_sr):
        vm = vm_on_iscsi_sr
        vdi = get_vdi_from_vm(vm)

        vm.shutdown(verify=True)
        enable_cbt(vdi)

        assert get_cbt_enabled(vdi) == 'true'

    def test_snapshot_on_iscsi_with_cbt(self, host, vm_on_iscsi_sr):
        vm = vm_on_iscsi_sr
        vdi = get_vdi_from_vm(vm)

        vm.shutdown(verify=True)
        enable_cbt(vdi)

        snapshot = vm.snapshot()
        assert snapshot.vdi_uuids()
        snapshot.destroy(verify=True)

    def test_thin_provisioning_with_cbt(self, host, iscsi_sr):
        device_config = iscsi_device_config()
        device_config['allocation'] = 'thin'

        sr_thin = host.sr_create('lvmoiscsi', 'LVMOISCSI-thin-CBT', device_config, verify=True)
        vm = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=sr_thin.uuid)
        vdi = get_vdi_from_vm(vm)

        enable_cbt(vdi)
        assert get_cbt_enabled(vdi) == 'true'

        vm.destroy(verify=True)
        sr_thin.destroy(verify=True)

    def test_multiple_vms_with_cbt_on_iscsi(self, host, iscsi_sr):
        vm1 = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=iscsi_sr.uuid)
        vm2 = host.import_vm(vm_image('mini-linux-x86_64-bios'), sr_uuid=iscsi_sr.uuid)

        vdi1 = get_vdi_from_vm(vm1)
        vdi2 = get_vdi_from_vm(vm2)

        enable_cbt(vdi1)
        enable_cbt(vdi2)

        assert get_cbt_enabled(vdi1) == 'true'
        assert get_cbt_enabled(vdi2) == 'true'

        vm1.destroy(verify=True)
        vm2.destroy(verify=True)

    def test_snapshot_coalesce_iscsi_with_cbt(self, host, vm_on_iscsi_sr):
        vm = vm_on_iscsi_sr
        vdi = get_vdi_from_vm(vm)

        vm.shutdown(verify=True)
        enable_cbt(vdi)

        snapshot = vm.snapshot()
        vm.start()
        vm.wait_for_os_booted()
        vm.ssh(['dd', 'if=/dev/urandom', 'of=/tmp/test', 'bs=1M', 'count=10'])
        vm.shutdown(verify=True)

        snapshot.destroy(verify=True)
        vdi.wait_for_coalesce()

        assert get_cbt_enabled(vdi) == 'true'

    @pytest.mark.slow
    def test_performance_iscsi_cbt(self, host, vm_on_iscsi_sr):
        vm = vm_on_iscsi_sr
        vdi = get_vdi_from_vm(vm)

        vm.shutdown(verify=True)
        enable_cbt(vdi)

        baseline = vm.snapshot()
        baseline_vdi = baseline.vdi_uuids()[0]

        vm.start()
        vm.wait_for_os_booted()
        vm.ssh(['dd', 'if=/dev/urandom', 'of=/tmp/large', 'bs=1M', 'count=50'])
        vm.shutdown(verify=True)

        changed = list_changed_blocks(vdi, baseline_vdi)
        assert changed

        baseline.destroy(verify=True)

    @pytest.mark.reboot
    def test_iscsi_cbt_persist_after_reboot(self, host, vm_on_iscsi_sr):
        vm = vm_on_iscsi_sr
        vdi = get_vdi_from_vm(vm)

        vm.shutdown(verify=True)
        enable_cbt(vdi)

        host.reboot(verify=True)
        host.ssh(['xe-toolstack-restart'])
        wait_for(lambda: vm.exists(), "Wait for VM")
        assert get_cbt_enabled(vdi) == 'true'
