import logging
import pytest

from lib.common import wait_for_not

@pytest.mark.multi_vms
class Test:
    def test_pause(self, running_vm):
        vm = running_vm
        vm.pause(verify=True)
        vm.unpause()
        vm.wait_for_vm_running_and_ssh_up()

    def test_suspend(self, running_vm):
        vm = running_vm
        vm.suspend(verify=True)
        vm.resume()
        vm.wait_for_vm_running_and_ssh_up()

    def test_snapshot(self, running_vm):
        vm = running_vm
        vm.test_snapshot_on_running_vm()

    def test_checkpoint(self, running_vm):
        vm = running_vm
        logging.info("Start a 'sleep' process on VM through SSH")
        pid = vm.start_background_process('sleep 10000')
        snapshot = vm.checkpoint()
        filepath = '/tmp/%s' % snapshot.uuid
        vm.ssh_touch_file(filepath)
        snapshot.revert()
        vm.resume()
        vm.wait_for_vm_running_and_ssh_up()
        logging.info("Check file does not exist anymore")
        vm.ssh(['test ! -f ' + filepath])
        logging.info("Check 'sleep' process is still running")
        assert vm.pid_exists(pid)
        logging.info("Kill background process")
        vm.ssh(['kill ' + pid])
        wait_for_not(lambda: vm.pid_exists(pid), "Wait for process %s not running anymore" % pid)
        snapshot.destroy(verify=True)
