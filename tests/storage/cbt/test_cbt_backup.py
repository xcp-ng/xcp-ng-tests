from __future__ import annotations

import pytest

from tests.storage.cbt.conftest import (
    get_cbt_enabled,
    get_vdi_from_vm,
    list_changed_blocks,
)

class TestCBTBackup:

    def test_incremental_backup_workflow(self, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        full_backup = vm.snapshot()
        full_backup_vdi = full_backup.vdi_uuids()[0]

        vm.start()
        vm.wait_for_os_booted()
        vm.ssh(['dd', 'if=/dev/urandom', 'of=/tmp/data1', 'bs=1M', 'count=10'])
        vm.shutdown(verify=True)

        inc1 = vm.snapshot()
        changed1 = list_changed_blocks(vdi, full_backup_vdi)
        assert changed1

        vm.start()
        vm.wait_for_os_booted()
        vm.ssh(['dd', 'if=/dev/urandom', 'of=/tmp/data2', 'bs=1M', 'count=10'])
        vm.shutdown(verify=True)

        inc2 = vm.snapshot()
        inc1_vdi = inc1.vdi_uuids()[0]
        changed2 = list_changed_blocks(vdi, inc1_vdi)
        assert changed2

        full_backup.destroy(verify=True)
        inc1.destroy(verify=True)
        inc2.destroy(verify=True)

    def test_backup_chain_with_snapshot_deletion(self, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        snapshots = []
        for i in range(3):
            vm.start()
            vm.wait_for_os_booted()
            vm.ssh(['dd', 'if=/dev/urandom', f'of=/tmp/data{i}', 'bs=1M', 'count=5'])
            vm.shutdown(verify=True)

            snapshot = vm.snapshot()
            snapshots.append(snapshot)

        snapshots[1].destroy(verify=True)
        vdi.wait_for_coalesce()

        assert get_cbt_enabled(vdi) == 'true'

        changed = list_changed_blocks(vdi, snapshots[0].vdi_uuids()[0])
        assert changed

        snapshots[0].destroy(verify=True)
        snapshots[2].destroy(verify=True)

    def test_full_backup_after_multiple_incrementals(self, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        snapshots = []
        for i in range(5):
            vm.start()
            vm.wait_for_os_booted()
            vm.ssh(['dd', 'if=/dev/urandom', f'of=/tmp/data{i}', 'bs=1M', 'count=5'])
            vm.shutdown(verify=True)
            snapshots.append(vm.snapshot())

        new_full = vm.snapshot()
        assert get_cbt_enabled(vdi) == 'true'

        for snapshot in snapshots:
            snapshot.destroy(verify=True)
        new_full.destroy(verify=True)

    @pytest.mark.slow
    def test_backup_performance_large_changes(self, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        baseline = vm.snapshot()
        baseline_vdi = baseline.vdi_uuids()[0]

        vm.start()
        vm.wait_for_os_booted()
        vm.ssh(['dd', 'if=/dev/urandom', 'of=/tmp/large', 'bs=1M', 'count=100'])
        vm.shutdown(verify=True)

        changed = list_changed_blocks(vdi, baseline_vdi)
        assert changed

        baseline.destroy(verify=True)
