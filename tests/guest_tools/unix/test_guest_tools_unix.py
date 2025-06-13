import logging
import time

import pytest

from lib.common import PackageManagerEnum, wait_for

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.0
# - hostA2: Second member of the pool. Can have any local SR. No need to specify it on CLI.
# From --vm parameter
# - A VM to import, supported by the Linux/install.sh script of the guest tools ISO
#   (without this flag you get an alpine, and that is not suitable)

class State:
    def __init__(self):
        self.tools_version = None
        self.vm_distro = None

@pytest.mark.multi_vms
@pytest.mark.usefixtures("unix_vm")
class TestGuestToolsUnix:
    @pytest.fixture(scope='class')
    def state(self):
        return State()

    def _check_tools_version(self, vm, tools_version):
        logging.info("Check that the detected tools version is '%s'" % tools_version)
        detected_version = vm.tools_version()
        assert detected_version == tools_version

    def _check_os_info(self, vm, vm_distro):
        logging.info("Check that the detected distro is '%s'" % vm_distro)
        detected_distro = vm.distro()
        assert detected_distro == vm_distro

    @pytest.fixture(scope="class", autouse=True)
    def vm_install(self, running_vm, state):
        vm = running_vm

        # skip test for some unixes
        state.vm_distro = vm.distro()
        if state.vm_distro == "alpine":
            pytest.skip('Alpine not supported by the guest tools installation script at the moment')

        print("Check that we are able to detect that xe-daemon is running")
        vm.ssh(['pgrep', '-f', 'xe-daemon'])

        # remove the installed tools
        logging.info("Detect package manager and uninstall the guest tools")
        pkg_mgr = vm.detect_package_manager()
        if pkg_mgr == PackageManagerEnum.RPM:
            # Our guest tools come in two packages for RPM distros: xe-guest-utilities and xe-guest-utilities-xenstore.
            # However, the following implementation will also work for a single xe-guest-utilities RPM.
            vm.execute_script('rpm -qa | grep xe-guest-utilities | xargs rpm -e')
        elif pkg_mgr == PackageManagerEnum.APT_GET:
            vm.ssh(['apt-get', 'remove', '-y', 'xe-guest-utilities'])
        else:
            pytest.skip("Package manager '%s' not supported in this test" % pkg_mgr)

        # check that xe-daemon is not running anymore
        assert vm.ssh_with_result(['pgrep', '-f', 'xe-daemon']).returncode != 0, \
            "xe-daemon must not be running anymore"

        # mount ISO
        logging.info("Mount guest tools ISO")
        vm.insert_guest_tools_iso()
        tmp_mnt = vm.ssh(['mktemp', '-d'])
        time.sleep(2) # wait a small amount of time just to ensure the device is available
        vm.ssh(['mount', '-t', 'iso9660', '/dev/cdrom', tmp_mnt])

        # get tools version number for future checks
        prefix = 'xe-guest-utilities_'
        suffix = '_x86_64.tgz'
        tgz_filename = vm.ssh(['find', tmp_mnt, '-name', prefix + '*' + suffix])
        state.tools_version = tgz_filename.split('/')[-1][len(prefix):-len(suffix)]

        # install tools
        logging.info("Install tools %s using install.sh" % state.tools_version)
        vm.ssh([tmp_mnt + '/Linux/install.sh', '-n'])

        # unmount ISO
        logging.info("Unmount guest tools ISO")
        vm.ssh(['umount', tmp_mnt])
        vm.eject_cd()

        # check that xe-daemon is running
        wait_for(lambda: vm.ssh_with_result(['pgrep', '-f', 'xe-daemon']).returncode == 0,
                 "Wait for xe-daemon running")

    def test_check_tools(self, running_vm, state):
        vm = running_vm
        self._check_tools_version(vm, state.tools_version)
        self._check_os_info(vm, state.vm_distro)

    def test_check_tools_after_reboot(self, running_vm, state):
        vm = running_vm
        vm.reboot(verify=True)
        self._check_tools_version(vm, state.tools_version)
        self._check_os_info(vm, state.vm_distro)

    def test_xenstore(self, running_vm):
        logging.info("Testing various xenstore commands from the guest")
        vm = running_vm
        vm.ssh(['xenstore-ls'])
        vm.ssh(['xenstore-exists', 'vm'])
        vm.ssh(['xenstore-list', 'data'])
        assert vm.ssh(['xenstore-read', 'vm']) == '/vm/%s' % vm.uuid
        vm.ssh(['xenstore-write', 'data/test-xcp-ng', 'Test'])
        assert vm.ssh(['xenstore-read', 'data/test-xcp-ng']) == 'Test'
        vm.ssh(['xenstore-rm', 'data/test-xcp-ng'])
        assert vm.ssh_with_result(['xenstore-exists', 'data/test-xcp-ng']).returncode != 0

    def test_clean_shutdown(self, running_vm):
        vm = running_vm
        vm.shutdown(verify=True)
        # restore VM state
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()

    def test_storage_migration(self, running_vm, host, hostA2, local_sr_on_hostA2, state):
        vm = running_vm
        # migrate to default SR on hostA2
        vm.migrate(hostA2, local_sr_on_hostA2)
        wait_for(lambda: vm.all_vdis_on_sr(local_sr_on_hostA2), "Wait for all VDIs on destination SR")
        wait_for(lambda: vm.is_running_on_host(hostA2), "Wait for VM to be running on destination host")
        self._check_tools_version(vm, state.tools_version)
        self._check_os_info(vm, state.vm_distro)
        # We don't migrate the VM back since the test module ends here and vm fixtures are module-scoped.
        # However, if in the future we manage to mutualize VMs for different test modules, then
        # we will need to either migrate it back, or create a throw-away clone before migrating.
