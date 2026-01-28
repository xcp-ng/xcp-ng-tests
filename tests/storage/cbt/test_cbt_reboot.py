from __future__ import annotations

import pytest

from lib.common import wait_for
from tests.storage.cbt.conftest import (
    get_cbt_enabled,
    get_vdi_from_vm,
    list_changed_blocks,
)

@pytest.mark.reboot
class TestCBTReboot:
    @pytest.mark.reboot
    def test_cbt_persists_after_host_reboot(self, host, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)
        host.reboot(verify=True)
        host.ssh(['xe-toolstack-restart'])
        wait_for(lambda: vm.exists(), "Wait for VM")

        assert get_cbt_enabled(vdi) == 'true'

    @pytest.mark.reboot
    def test_cbt_functionality_after_host_reboot(self, host, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        snapshot_before = vm.snapshot()
        snapshot_before_vdi = snapshot_before.vdi_uuids()[0]

        host.reboot(verify=True)
        host.ssh(['xe-toolstack-restart'])
        wait_for(lambda: vm.exists(), "Wait for VM")

        vm.start()
        vm.wait_for_os_booted()
        vm.ssh(['dd', 'if=/dev/urandom', 'of=/tmp/test', 'bs=1M', 'count=10'])
        vm.shutdown(verify=True)

        changed = list_changed_blocks(vdi, snapshot_before_vdi)
        assert changed
        snapshot_before.destroy(verify=True)

    @pytest.mark.reboot
    def test_vm_crash_recovery_with_cbt(self, host, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        vm.start()
        vm.wait_for_os_booted()
        vm.ssh(['reboot'], check=False)
        wait_for(lambda: vm.is_running(), "Wait for VM restart")
        assert get_cbt_enabled(vdi) == 'true'
