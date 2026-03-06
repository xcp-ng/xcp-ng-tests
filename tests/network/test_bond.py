from __future__ import annotations

import pytest

import logging

from lib.bond import Bond
from lib.host import Host
from lib.network import Network
from lib.vm import VM

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host, with at least 3 NICs (1 management, 2 unused), with tcpdump
# From --vm parameter
# - A VM to import (alpine expected)

@pytest.mark.small_vm
class TestNetwork:
    @pytest.mark.no_vm
    def test_basic(self, host: Host, empty_network: Network):
        assert empty_network.pif_uuids() == [], "PIF list must be empty"
        assert empty_network.vif_uuids() == [], "VIF list must be empty"
        assert empty_network.is_private(), "empty_network must be private"
        assert empty_network.MTU() == 1500, "unexpected MTU"

    def test_private_network(self, host: Host, empty_network: Network, imported_vm: VM):
        network = empty_network

        try:
            vm1 = imported_vm.clone()
            vm2 = imported_vm.clone()

            vif_1_1 = vm1.create_vif(1, network_uuid=network.uuid)
            vif_2_1 = vm2.create_vif(1, network_uuid=network.uuid)

            assert len(vm1.vifs()) == 2, "VM1 should have 2 NICs"
            assert len(vm2.vifs()) == 2, "VM2 should have 2 NICs"
            assert len(network.vif_uuids()) == 2, "unexpected number of VIFs in network"

            vm1.start()
            vm2.start()

            vm1.wait_for_vm_running_and_ssh_up()
            vm2.wait_for_vm_running_and_ssh_up()

            logging.info("Configuring local address on private network")
            vm1.ssh(f"ifconfig eth{vif_1_1.param_get('device')} inet 169.254.1.1 broadcast 169.254.0.0 up")
            vm2.ssh(f"ifconfig eth{vif_2_1.param_get('device')} inet 169.254.2.1 broadcast 169.254.0.0 up")

            logging.info("Ping VMs")
            assert vm1.ssh(["ping", "-c3", "-w5", "169.254.2.1"])
            assert vm2.ssh(["ping", "-c3", "-w5", "169.254.1.1"])

        finally:
            # VIFs are destroyed by VM.destroy()
            vm2.destroy()
            vm1.destroy()

def _wait_for_packet(host: Host | VM, interface: str, sfilter: str, timeout: int = 30) -> None:
    host.ssh(f"timeout {timeout} tcpdump -i {interface} -n -c1 '{sfilter}'")

@pytest.mark.complex_prerequisites
@pytest.mark.small_vm
@pytest.mark.parametrize("bond_devices", [["eth1", "eth2"]])
@pytest.mark.parametrize("bond_mode", ["lacp", "active-backup", "balance-slb"])
def test_bond(host: Host, imported_vm: VM, bond_devices: list[str], bond_mode: str, bond: Bond):
    # expect host with eth1 and eth2 NICs free for use
    logging.info(f"Bond = {bond.uuid} mode={bond.mode()} slaves={bond.slaves()}")

    # disable LACP fallback (make Bond to require LACP negociation first)
    if bond_mode == "lacp":
        bond.param_set("properties", key="lacp-fallback-ab", value=False)

    # check bond0 on the host
    stdout = host.ssh("ovs-appctl bond/show bond0")

    if bond_mode == 'lacp':
        bond_mode_output = 'balance-tcp'
    else:
        bond_mode_output = bond_mode

    if f"bond_mode: {bond_mode_output}" not in stdout:
        pytest.fail(f"unexpected bond_mode: {bond_mode}: stdout={stdout}")

    if bond_mode == 'lacp':
        if "lacp_fallback_ab: false" not in stdout:
            pytest.fail(f"unexpected lacp_fallback_ab: stdout={stdout}")
        elif "lacp_status: configured" not in stdout and "lacp_status: negotiated" not in stdout:
            pytest.fail(f"unexpected lacp_status: stdout={stdout}")
    else:
        if "lacp_status: off" not in stdout:
            pytest.fail(f"unexpected lacp_status: stdout={stdout}")

    try:
        # on the VM, add a new NIC using the bond
        vm = imported_vm.clone()
        vm.create_vif(1, network_uuid=bond.master().network_uuid())
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()

        vm.ssh("apk add tcpdump")
        vm.ssh("ip link set eth1 up")

        if bond_mode == 'lacp':
            # we are checking if we are seeing LACP packet on VM side.
            #
            # OpenvSwitch will send LACP negociation on host.eth1 and host.eth2
            # to etablish LACP link. As we don't have other side, it will keep
            # sending them.
            # On VM side, we could see such packets. So we are checking the VM.eth1
            # is properly connected to the Bond just created (but we don't check that
            # OpenvSwitch is properly setup)
            logging.info("Waiting for LACP packet")
            _wait_for_packet(vm, "eth1", "ether proto 0x8809")

        else:
            # just check if we see a packet on the host side.
            # theVM kernel is expected to send IPv6 packet for Router Solicitation
            # as the VM interface is UP.
            logging.info("Waiting for some IPv6 packet")
            _wait_for_packet(host, "eth1", "ether proto 0x86dd")

    finally:
        vm.destroy()
