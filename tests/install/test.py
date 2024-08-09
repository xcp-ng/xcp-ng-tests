import logging
import pytest

from lib import installer

from data import NETWORKS
assert "MGMT" in NETWORKS

# Requirements:
# - one XCP-ng host capable of nested virt, with an ISO SR, and a default SR

class TestNested:
    @pytest.mark.iso_version("821.1")
    @pytest.mark.vm_definitions(
        dict(
            name="vm1",
            template="Other install media",
            params=(
                # dict(param_name="", value=""),
                dict(param_name="memory-static-max", value="4GiB"),
                dict(param_name="memory-dynamic-max", value="4GiB"),
                dict(param_name="memory-dynamic-min", value="4GiB"),
                dict(param_name="VCPUs-max", value="2"),
                dict(param_name="VCPUs-at-startup", value="2"),
                dict(param_name="platform", key="exp-nested-hvm", value="true"), # FIXME < 8.3 host?
                dict(param_name="HVM-boot-params", key="firmware", value="uefi"),
                dict(param_name="HVM-boot-params", key="order", value="dc"),
                dict(param_name="platform", key="device-model", value="qemu-upstream-uefi"),
            ),
            vdis=[dict(name="vm1 system disk", size="100GiB", device="xvda", userdevice="0")],
            cd_vbd=dict(device="xvdd", userdevice="3"),
            vifs=[dict(index=0, network_name=NETWORKS["MGMT"])],
        ))
    def test_install(self, vm_booted_with_installer):
        host_vm = vm_booted_with_installer
        installer.monitor_install(ip=host_vm.ip)
