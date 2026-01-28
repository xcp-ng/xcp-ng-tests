from __future__ import annotations

import pytest

from tests.storage.cbt.conftest import (
    disable_cbt_on_vdi,
    enable_cbt_on_vdi,
    get_cbt_enabled,
    get_vdi_from_vm,
    verify_cbt_log_files_exist,
)

class TestCBTEnableDisable:

    def test_enable_cbt_on_stopped_vm(self, host, unix_vm):
        vm = unix_vm
        vdi = get_vdi_from_vm(vm)

        vm.shutdown(verify=True)
        enable_cbt_on_vdi(vdi)

        assert get_cbt_enabled(vdi) == 'true'
        assert verify_cbt_log_files_exist(vdi)

    def test_disable_cbt_on_stopped_vm(self, host, unix_vm):
        vm = unix_vm
        vdi = get_vdi_from_vm(vm)

        vm.shutdown(verify=True)
        enable_cbt_on_vdi(vdi)
        disable_cbt_on_vdi(vdi)

        assert get_cbt_enabled(vdi) == 'false'

    def test_enable_cbt_idempotent(self, host, unix_vm):
        vm = unix_vm
        vdi = get_vdi_from_vm(vm)

        vm.shutdown(verify=True)
        enable_cbt_on_vdi(vdi)
        enable_cbt_on_vdi(vdi)

        assert get_cbt_enabled(vdi) == 'true'

    def test_disable_cbt_idempotent(self, host, unix_vm):
        vm = unix_vm
        vdi = get_vdi_from_vm(vm)

        vm.shutdown(verify=True)
        enable_cbt_on_vdi(vdi)
        disable_cbt_on_vdi(vdi)
        disable_cbt_on_vdi(vdi)

        assert get_cbt_enabled(vdi) == 'false'


class TestCBTBasicOperations:

    def test_vm_start_stop_with_cbt(self, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        vm.start()
        vm.wait_for_os_booted()
        assert get_cbt_enabled(vdi) == 'true'

        vm.shutdown(verify=True)
        assert get_cbt_enabled(vdi) == 'true'

    def test_vm_reboot_with_cbt(self, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        vm.start()
        vm.wait_for_os_booted()
        vm.reboot(verify=True)
        assert get_cbt_enabled(vdi) == 'true'

    def test_disable_cbt_on_running_vm(self, running_vm_with_cbt):
        vm = running_vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        with pytest.raises(Exception):
            disable_cbt_on_vdi(vdi)

    def test_enable_cbt_on_running_vm(self, host, unix_vm):
        vm = unix_vm
        vdi = get_vdi_from_vm(vm)

        vm.start()
        vm.wait_for_os_booted()

        with pytest.raises(Exception):
            enable_cbt_on_vdi(vdi)
