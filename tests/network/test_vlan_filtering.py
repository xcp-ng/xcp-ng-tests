from __future__ import annotations

import pytest

from lib.common import Defer
from lib.network import Network
from lib.vm import VM

from . import tcpdump

# Requirements:
# From --hosts parameter:
# - host(A1): an XCP-ng host
# From --vm parameter
# - A VM to import

def start_vm_on_trunk(
    defer: Defer, vm: VM, network: Network, clone_vm: bool, name: str, trunks: str = ""
) -> tuple[VM, str]:
    if clone_vm:
        vm = vm.clone()
        defer(lambda: vm.destroy())

    vm.param_set("name-label", name)

    n = len(vm.vifs())
    iface = f"eth{n}"
    vif = vm.create_vif(n, network_uuid=network.uuid)

    vif.param_set("trunks", trunks)

    vm.start()
    vm.wait_for_vm_running_and_ssh_up()
    vm.ssh(f"ip link set {iface} up")

    return vm, iface

@pytest.mark.small_vm
class TestBasic:

    def test_passing(self, defer: Defer, empty_network: Network, imported_vm: VM, vm_with_tcpdump_scope_function: VM):
        vm_writer, ifaceW = start_vm_on_trunk(
            defer,
            vm=imported_vm,
            network=empty_network,
            clone_vm=True,
            name="test_passing: vm_writer",
        )

        vm_reader, ifaceR = start_vm_on_trunk(
            defer,
            vm=vm_with_tcpdump_scope_function,
            network=empty_network,
            clone_vm=False,
            name="test_passing: vm_reader",
            trunks="42",
        )

        # configure VLAN 42 on vm_writer
        vm_writer.ssh(f"ip link add link {ifaceW} name {ifaceW}.42 type vlan id 42")
        vm_writer.ssh(f"ip addr add 192.168.42.1/24 dev {ifaceW}.42")
        vm_writer.ssh(f"ip link set {ifaceW}.42 up")

        with tcpdump(defer, "tcpdump.pcap", vm_reader, ifaceR, "vlan 42 and arp", count=1):
            # send some packets on VLAN 42 (ARP packets will be send)
            vm_writer.ssh("ping -c1 -w1 192.168.42.2", check=False)

        # check packets are seen on vm_reader (trunks=42)
        vm_reader.ssh("test $(tcpdump -n -r tcpdump.pcap | wc -l) -gt 0")

    def test_filtered(self, defer: Defer, empty_network: Network, imported_vm: VM, vm_with_tcpdump_scope_function: VM):
        vm_writer, ifaceW = start_vm_on_trunk(
            defer,
            vm=imported_vm,
            network=empty_network,
            clone_vm=True,
            name="test_filtered: vm_writer",
        )

        vm_reader, ifaceR = start_vm_on_trunk(
            defer,
            vm=vm_with_tcpdump_scope_function,
            network=empty_network,
            clone_vm=False,
            name="test_filtered: vm_reader",
            trunks="52",
        )

        # configure VLAN 42 on vm_writer
        vm_writer.ssh(f"ip link add link {ifaceW} name {ifaceW}.42 type vlan id 42")
        vm_writer.ssh(f"ip addr add 192.168.42.1/24 dev {ifaceW}.42")
        vm_writer.ssh(f"ip link set {ifaceW}.42 up")

        with tcpdump(defer, "tcpdump.pcap", vm_reader, ifaceR, "vlan 42 and arp", count=1):
            # send some packets on VLAN 42 (ARP packets will be send)
            vm_writer.ssh("ping -c1 -w1 192.168.42.2", check=False)

        # check packets are *NOT* seen on vm_reader (trunks=52)
        vm_reader.ssh("test $(tcpdump -n -r tcpdump.pcap | wc -l) -eq 0")
