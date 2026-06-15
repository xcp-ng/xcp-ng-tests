import pytest

import logging

from lib.common import wait_for_not
from lib.vm import VM

@pytest.mark.multi_vms
class Test:
    def test_pause(self, running_vm: VM) -> None:
        vm = running_vm
        vm.pause(verify=True)
        vm.unpause()
        vm.wait_for_vm_running_and_ssh_up()

    def test_suspend(self, running_vm: VM) -> None:
        vm = running_vm
        vm.suspend(verify=True)
        vm.resume()
        vm.wait_for_vm_running_and_ssh_up()

    def test_snapshot(self, running_vm: VM) -> None:
        vm = running_vm
        vm.test_snapshot_on_running_vm()

    # When running the tests on Windows, the background ssh process is never terminated
    # This results in a ResourceWarning
    @pytest.mark.filterwarnings("ignore::ResourceWarning")
    def test_checkpoint(self, running_vm: VM) -> None:
        vm = running_vm
        logging.info("Start a 'sleep' process on VM through SSH")
        if vm.is_windows:
            pid = vm.start_background_powershell('Start-Sleep -Seconds 10000')
        else:
            pid = vm.start_background_process('sleep 10000')
        logging.info(f"Background task PID: {pid}")
        snapshot = vm.checkpoint()
        filepath = '/tmp/%s' % snapshot.uuid
        vm.ssh_touch_file(filepath)
        snapshot.revert()
        vm.resume()
        vm.wait_for_vm_running_and_ssh_up()
        logging.info("Check file does not exist anymore")
        vm.ssh(f'test ! -f {filepath}')
        logging.info("Check 'sleep' process is still running")
        assert vm.pid_exists(pid, winpid=True)
        logging.info("Kill background process")
        vm.kill_pid(pid, winpid=True)
        wait_for_not(lambda: vm.pid_exists(pid, winpid=True), "Wait for process %s not running anymore" % pid)
        snapshot.destroy(verify=True)
