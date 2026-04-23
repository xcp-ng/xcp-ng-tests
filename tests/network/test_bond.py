from __future__ import annotations

import pytest

import logging

from lib.bond import Bond
from lib.host import Host
from lib.pif import PIF
from lib.vm import VM

# Requirements:
# From --hosts parameter:
# - host(A1): an XCP-ng host, with at least 2 free NICs
# From --vm parameter
# - A VM to import

def _wait_for_packet(host: Host | VM, interface: str | list[str], sfilter: str, timeout: int = 30) -> None:
    if isinstance(interface, list):
        interface = ' '.join([f"-i {iface}" for iface in interface])
    else:
        interface = f"-i {interface}"

    host.ssh(f"timeout {timeout} tcpdump {interface} -n -c1 '{sfilter}'")

@pytest.mark.complex_prerequisites
@pytest.mark.small_vm
class TestBond:
    def test_lacp(self, host: Host, vm_with_tcpdump_scope_function: VM, bond_lacp: Bond):
        # expect host with eth1 and eth2 NICs free for use
        logging.info(f"Bond = {bond_lacp.uuid} mode={bond_lacp.mode()} \
            slaves={bond_lacp.slaves()}")

        # disable LACP fallback (make Bond to require LACP negociation first)
        bond_lacp.param_set("properties", key="lacp-fallback-ab", value="false")

        # check bond0 on the host
        output = host.ssh("ovs-appctl bond/show bond0")

        if "bond_mode: balance-tcp" not in output:
            pytest.fail(f"string 'bond_mode: balance-tcp' not found in output. output={output}")
        elif "lacp_fallback_ab: false" not in output:
            pytest.fail(f"unexpected lacp_fallback_ab: output={output}")
        elif "lacp_status: configured" not in output and "lacp_status: negotiated" not in output:
            pytest.fail(f"unexpected lacp_status: output={output}")

        # on the VM, add a new NIC using the bond
        vm = vm_with_tcpdump_scope_function
        vm.create_vif(1, network_uuid=bond_lacp.master().network_uuid())
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()

        vm.ssh("ip link set eth1 up")

        # we are checking if we are seeing LACP packet on *VM side*.
        #
        # OpenvSwitch will send LACP negociation on host.eth1 and host.eth2
        # to establish LACP link. As we don't have other side, it will keep
        # sending them.
        # On VM side, we could see such packets. So we are checking the VM.eth1
        # is properly connected to the Bond just created (but we don't check that
        # OpenvSwitch is properly setup)
        logging.info("Waiting for LACP packet")
        _wait_for_packet(vm, "eth1", "ether proto 0x8809")

    def test_active_backup(self, host: Host, vm_with_tcpdump_scope_function: VM, bond_activebackup: Bond):
        # expect host with eth1 and eth2 NICs free for use
        logging.info(f"Bond = {bond_activebackup.uuid} mode={bond_activebackup.mode()} \
            slaves={bond_activebackup.slaves()}")

        # get device's PIFs used by the bond on the host
        bond_devices = [PIF(uuid, host).device() for uuid in bond_activebackup.slaves()]

        # check bond0 on the host
        output = host.ssh("ovs-appctl bond/show bond0")

        if "bond_mode: active-backup" not in output:
            pytest.fail(f"string 'bond_mode: active-backup' not found in output. output={output}")

        if "lacp_status: off" not in output:
            pytest.fail(f"unexpected lacp_status: output={output}")

        # on the VM, add a new NIC using the bond
        vm = vm_with_tcpdump_scope_function
        vm.create_vif(1, network_uuid=bond_activebackup.master().network_uuid())
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()

        vm.ssh("ip link set eth1 up")

        # just check if we see a packet on the *host side*.
        # the VM kernel is expected to send IPv6 packet for Router Solicitation
        # as the VM interface is UP.
        logging.info("Waiting for some IPv6 packet")
        _wait_for_packet(host, bond_devices, "ether proto 0x86dd")

    def test_balance_slb(self, host: Host, vm_with_tcpdump_scope_function: VM, bond_balanceslb: Bond):
        # expect host with eth1 and eth2 NICs free for use
        logging.info(f"Bond = {bond_balanceslb.uuid} mode={bond_balanceslb.mode()} \
            slaves={bond_balanceslb.slaves()}")

        # get device's PIFs used by the bond on the host
        bond_devices = [PIF(uuid, host).device() for uuid in bond_balanceslb.slaves()]

        # check bond0 on the host
        output = host.ssh("ovs-appctl bond/show bond0")

        if "bond_mode: balance-slb" not in output:
            pytest.fail(f"string 'bond_mode: balance-tcp' not found in output. output={output}")

        if "lacp_status: off" not in output:
            pytest.fail(f"unexpected lacp_status: output={output}")

        # on the VM, add a new NIC using the bond
        vm = vm_with_tcpdump_scope_function
        vm.create_vif(1, network_uuid=bond_balanceslb.master().network_uuid())
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()

        vm.ssh("ip link set eth1 up")

        # just check if we see a packet on the *host side*.
        # the VM kernel is expected to send IPv6 packet for Router Solicitation
        # as the VM interface is UP.
        logging.info("Waiting for some IPv6 packet")
        _wait_for_packet(host, bond_devices, "ether proto 0x86dd")
