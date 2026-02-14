import pytest

from lib.commands import SSHCommandFailed
from lib.vif import VIF
from lib.vm import VM

# Requirements:
# - one XCP-ng host (--hosts)

def count_interfaces(vm: VM) -> int:
    """
    Returns the number of interfaces returned by 'ip link show'
    """
    ip_link_show = vm.ssh_with_result(['ip', 'link', 'show'])
    assert ip_link_show.returncode == 0

    stdout = ip_link_show.stdout
    assert isinstance(stdout, str)
    return stdout.count('link/ether')

@pytest.mark.small_vm
class TestVIFManagement:
    def test_vif_management(self, running_unix_vm: VM):
        vm = running_unix_vm
        host = vm.host
        network_uuid = host.management_network()

        # number of VIFs in the VM
        n_vif = len(vm.vifs())

        # number of interfaces in the VM
        n_interfaces = count_interfaces(vm)

        # create a new VIF in the management network
        vif_new = vm.create_vif(n_vif, network_uuid=network_uuid)

        try:
            # check one more VIF
            assert len(vm.vifs()) == n_vif + 1
            assert count_interfaces(vm) == n_interfaces

            # plug the VIF
            vif_new.plug()
            assert count_interfaces(vm) == n_interfaces + 1

            # try destroying the plugged VIF (should fail)
            try:
                vif_new.destroy()
                pytest.fail("VIF destroy should fail if the VIF is plugged")
            except SSHCommandFailed as exc:
                if "You attempted an operation that was not allowed." in exc.stdout:
                    pass
                else:
                    raise exc

            # unplug the VIF
            vif_new.unplug()
            assert count_interfaces(vm) == n_interfaces

        finally:
            # destroy the just created VIF
            vif_new.destroy()

        # check one less VIF
        assert len(vm.vifs()) == n_vif
        assert count_interfaces(vm) == n_interfaces
