from __future__ import annotations

import pytest

from tests.storage.cbt.conftest import (
    get_cbt_enabled,
    get_vdi_from_vm,
    list_changed_blocks,
)

class TestCBTSnapshots:

    def test_snapshot_with_cbt_enabled(self, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        snapshot = vm.snapshot()
        snapshot_vdi_uuids = snapshot.vdi_uuids()

        assert snapshot_vdi_uuids
        assert get_cbt_enabled(vdi) == 'true'

        snapshot.destroy(verify=True)

    def test_changed_blocks_between_snapshots(self, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        snapshot1 = vm.snapshot()
        snapshot1_vdi = snapshot1.vdi_uuids()[0]

        vm.start()
        vm.wait_for_os_booted()
        vm.ssh(['dd', 'if=/dev/urandom', 'of=/tmp/test', 'bs=1M', 'count=10'])
        vm.shutdown(verify=True)

        snapshot2 = vm.snapshot()

        changed_blocks = list_changed_blocks(vdi, snapshot1_vdi)
        assert changed_blocks

        snapshot1.destroy(verify=True)
        snapshot2.destroy(verify=True)

    def test_multiple_incremental_snapshots(self, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        snapshots = []
        for i in range(3):
            snapshot = vm.snapshot()
            snapshots.append(snapshot)

            vm.start()
            vm.wait_for_os_booted()
            vm.ssh(['dd', 'if=/dev/urandom', f'of=/tmp/test{i}', 'bs=1M', 'count=5'])
            vm.shutdown(verify=True)

        for i in range(len(snapshots) - 1):
            snapshot_vdi = snapshots[i].vdi_uuids()[0]
            changed_blocks = list_changed_blocks(vdi, snapshot_vdi)
            assert changed_blocks

        for snapshot in snapshots:
            snapshot.destroy(verify=True)

    def test_snapshot_coalesce_with_cbt(self, vm_with_cbt):
        vm = vm_with_cbt
        vdi = get_vdi_from_vm(vm)

        snapshot = vm.snapshot()

        vm.start()
        vm.wait_for_os_booted()
        vm.ssh(['dd', 'if=/dev/urandom', 'of=/tmp/test', 'bs=1M', 'count=10'])
        vm.shutdown(verify=True)

        snapshot.destroy(verify=True)
        vdi.wait_for_coalesce()

        assert get_cbt_enabled(vdi) == 'true'
