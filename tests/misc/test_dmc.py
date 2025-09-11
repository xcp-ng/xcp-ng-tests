import pytest

import logging
import time

from lib.common import wait_for
from lib.snapshot import Snapshot
from lib.vm import VM

from typing import Tuple

# Requirements:
# - XCP-ng >= 8.2.
#
# From --vm parameter:
# - A VM that supports ballooning.

MEMORY_TARGET_HIGH = 8 << 30
MEMORY_TARGET_LOW = 4 << 30


class DmcMemoryTracker:
    def __init__(self, vm: VM, memory_target: int):
        self._vm = vm
        self._memory_target = memory_target
        self._memory_actual = 0
        self._timestamp = 0.0
        self._update()

    def _update(self):
        """Update actual memory and register whether it has actually changed."""
        new_memory_actual = int(self._vm.param_get("memory-actual"))
        if new_memory_actual != self._memory_actual:
            self._memory_actual = new_memory_actual
            self._timestamp = time.perf_counter()

    def is_memory_target_satisfied(self):
        # memory-actual may not equal memory-target even if ballooning has finished.
        # Use a heuristic instead, where ballooning is determined to have finished if memory-actual is within 1% of
        # memory-target.
        return abs(self._memory_actual - self._memory_target) < self._memory_target // 100

    def poll(self):
        """Determine if ballooning has finished or stalled."""
        self._update()
        return self.is_memory_target_satisfied() or time.perf_counter() - self._timestamp > 10.0

    def memory_actual(self):
        return self._memory_actual


def wait_for_vm_balloon_finished(vm: VM):
    memory_target = int(vm.param_get("memory-target"))
    logging.info("Wait for ballooning to finish")
    tracker = DmcMemoryTracker(vm, memory_target)
    wait_for(tracker.poll, timeout_secs=300)
    logging.info(f"Current memory: {tracker.memory_actual()}")
    assert tracker.is_memory_target_satisfied()


@pytest.fixture(scope="module")
def imported_vm_and_snapshot(imported_vm: VM):
    vm = imported_vm
    snapshot = vm.snapshot()
    yield vm, snapshot
    snapshot.destroy(verify=True)


@pytest.fixture
def vm_with_memory_limits(imported_vm_and_snapshot: Tuple[VM, Snapshot]):
    vm, snapshot = imported_vm_and_snapshot
    vm.set_memory_limits(static_max=MEMORY_TARGET_HIGH, dynamic_min=MEMORY_TARGET_HIGH, dynamic_max=MEMORY_TARGET_HIGH)
    snapshot = vm.snapshot()
    yield vm
    snapshot.revert()


@pytest.mark.small_vm
class TestDmc:
    def start_dmc_vm(self, vm: VM):
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        if vm.param_get("other", "feature-balloon", accept_unknown_key=True) != "1":
            pytest.skip("VM does not support ballooning")

    def test_dmc_start_low(self, vm_with_memory_limits: VM):
        """Start the VM with less memory than the static max."""
        vm = vm_with_memory_limits
        vm.set_memory_target(MEMORY_TARGET_LOW)
        self.start_dmc_vm(vm)
        wait_for_vm_balloon_finished(vm)
        # restore
        vm.set_memory_target(MEMORY_TARGET_HIGH)
        wait_for_vm_balloon_finished(vm)

    def test_dmc_decrease(self, vm_with_memory_limits: VM):
        """Decrease the memory of a VM that started without DMC."""
        vm = vm_with_memory_limits
        self.start_dmc_vm(vm)
        vm.set_memory_target(MEMORY_TARGET_LOW)
        wait_for_vm_balloon_finished(vm)
        # restore
        vm.set_memory_target(MEMORY_TARGET_HIGH)
        wait_for_vm_balloon_finished(vm)

    def test_dmc_suspend_pod(self, vm_with_memory_limits: VM):
        """Suspend a VM with DMC and populate-on-demand enabled."""
        vm = vm_with_memory_limits
        vm.set_memory_target(MEMORY_TARGET_LOW)
        self.start_dmc_vm(vm)
        wait_for_vm_balloon_finished(vm)
        vm.suspend(verify=True)
        vm.resume()
        vm.wait_for_vm_running_and_ssh_up()

    def test_dmc_suspend(self, vm_with_memory_limits: VM):
        """Suspend a VM with DMC enabled."""
        vm = vm_with_memory_limits
        self.start_dmc_vm(vm)
        vm.set_memory_target(MEMORY_TARGET_LOW)
        wait_for_vm_balloon_finished(vm)
        vm.suspend(verify=True)
        vm.resume()
        vm.wait_for_vm_running_and_ssh_up()
