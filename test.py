import pytest
from lib.common import wait_for

pytestmark = pytest.mark.default_vm('mini-linux-x86_64-bios')

def test_pause(running_linux_vm):
    vm = running_linux_vm
    vm.pause()
    wait_for(vm.is_paused, "Wait for VM paused")
    vm.unpause()
    vm.wait_for_vm_running_and_ssh_up()

def test_suspend(running_linux_vm):
    vm = running_linux_vm
    vm.suspend()
    wait_for(vm.is_suspended, "Wait for VM suspended")
    vm.resume()
    vm.wait_for_vm_running_and_ssh_up()

def test_snapshot(running_linux_vm):
    vm = running_linux_vm
    vm.test_snapshot_on_linux_vm()

def test_checkpoint(running_linux_vm):
    vm = running_linux_vm
    print("Start a 'sleep' process on VM through SSH")
    vm.ssh(['sleep 100000'], background=True)
    snapshot = vm.checkpoint()
    filepath = '/tmp/%s' % snapshot.uuid
    vm.ssh_touch_file(filepath)
    snapshot.revert()
    vm.resume()
    vm.wait_for_vm_running_and_ssh_up()
    print("Check file does not exist anymore")
    vm.ssh(['test ! -f ' + filepath])
    print("Check 'sleep' process is still running")
    output = vm.ssh(['ps -edf | grep -v grep | grep "sleep 100000"'])
    print("Kill 'sleep' process")
    pid = output.split()[0]
    output = vm.ssh(['kill ' + pid])
    wait_for(lambda: vm.ssh(['! ps -edf | grep -s grep | grep "sleep 100000"'], check=False, simple_output=False).returncode != 0,
             "Wait for process %s not running anymore" % pid,
             timeout_secs=10)
    snapshot.destroy()
