from __future__ import annotations

import pytest

import logging

from lib.host import Host
from lib.network import Network
from lib.tunnel import Tunnel
from lib.vlan import VLAN
from lib.vm import VM
from lib.xo import xo_cli

# Requirements:
# xo-cli (on the host running the test) is expected to be already configured to see --hosts
# From --hosts parameter:
# - host(A1): first XCP-ng host, with at least 2 NICs (1 management, 1 unused)
# - host(A2): second XCP-ng host in the same pool (address will be automatically taken from hostA1)
# From --vm parameter
# - A VM to import

def pause(ms: int = 300) -> None:
    from time import sleep
    sleep(ms / 1000)

def ofctl_dumpflows(host: Host, br: str) -> list[str]:
    """
    Get the list of dump-flows installed for the bridge {br}
    """
    return host.ssh(
        f"ovs-ofctl -O OpenFlow11 dump-flows '{br}' | grep -F cookie=",
    ).splitlines()

def count_of(host: Host, br: str):
    """
    Return the number of OF flows in the bridge (excluding the default one)
    """
    return len(ofctl_dumpflows(host, br)) - 1

def ofproto_trace_drop_in_port(
    host: Host, br: str, flow: str, in_port: str,
    vlan_tag: int | None, vlan_device: str | None,
    log: bool,
) -> bool:
    if vlan_device == in_port:
        flow = f"in_port={in_port},vlan_vid={vlan_tag},{flow}"
    else:
        flow = f"in_port={in_port},{flow}"

    result = host.ssh(f"ovs-appctl ofproto/trace {br} {flow}")
    if log:
        action = 'drop' if result.endswith("Datapath actions: drop") else 'pass'
        logging.info(f"trace port='{in_port}' action='{action}': {result}")
    return result.endswith("Datapath actions: drop")

def ofproto_trace_drop(
    host: Host, br: str, flow: str,
    network_br: str | None = None,
    vlan_tag: int | None = None, vlan_device: str | None = None,
    log: bool = False,
) -> bool:
    def is_not_xapi_port(portname: str) -> bool:
        """
        Return False if portname has the form of "{br}_port"
        it is the internal communication port for linking hosts
        """
        return not portname.startswith(f"{br}_port")

    ports = host.ssh(f"ovs-vsctl list-ports {network_br or br}").splitlines()
    ports = list(filter(is_not_xapi_port, ports))
    if len(ports) == 0:
        # no ports on bridge, packet will pass
        return False

    if log:
        rules = host.ssh(f"ovs-ofctl -O OpenFlow11 dump-flows {br}")
        logging.info(f"rules '{br}': {rules}")

    return all([
        ofproto_trace_drop_in_port(
            host, br, flow, port,
            vlan_tag, vlan_device,
            log,
        )
        for port in ports
    ])


@pytest.mark.complex_prerequisites
@pytest.mark.small_vm
class TestSimple:
    def test_vifRule(self, connected_hosts_with_xo: list[Host], imported_vm: VM):
        host = connected_hosts_with_xo[0]
        vm = imported_vm.clone()
        try:
            vif = vm.vifs()[0]
            vifId = vif.uuid
            macAddress = vif.mac_address()
            hostBr = vif.network().bridge()

            assert count_of(host, hostBr) == 0, "no OF at init"

            # add OF rule (before starting VM)
            xo_cli('sdnController.addRule', {
                'vifId': vifId,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'tcp',
                'port': 'json:80',
                'allow': 'false',
            })

            # before starting the VM, traffic pass
            assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=80,dl_src={macAddress}")
            assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")

            # start the VM
            vm.start()

            pause()

            # just after starting the VM, the rule (80) apply (traffic drop)
            assert ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=80,dl_src={macAddress}")
            assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")

            # wait for XO to see the VM
            vm.wait_for_os_booted()

            # still right after VM fully booted
            assert ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=80,dl_src={macAddress}")
            assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")

            # add OF rule (while running)
            xo_cli('sdnController.addRule', {
                'vifId': vifId,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'tcp',
                'port': 'json:81',
                'allow': 'false',
            })

            # new rule added, both traffic dropped
            assert ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=80,dl_src={macAddress}")
            assert ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")

            # delete OF rule (while running)
            xo_cli('sdnController.deleteRule', {
                'vifId': vifId,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'tcp',
                'port': 'json:80',
            })

            # first rule deleted, traffic should pass (and 2nd rule drop traffic)
            assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=80,dl_src={macAddress}")
            assert ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")

            vm.shutdown(verify=True)

            # after shutdown, two rules removed (vif not here anymore)
            assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=80,dl_src={macAddress}")
            assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")

            vm.start()
            vm.wait_for_os_booted()

            # after restarted, only the second rule apply
            assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=80,dl_src={macAddress}")
            assert ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")

            # delete OF rule (while stopped)
            xo_cli('sdnController.deleteRule', {
                'vifId': vifId,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'tcp',
                'port': 'json:81',
            })

            # no more traffic blocked
            assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=80,dl_src={macAddress}")
            assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")

        finally:
            vm.destroy()

        assert count_of(host, hostBr) == 0, "no OF at init"

    def test_networkRule(self, connected_hosts_with_xo: list[Host], imported_vm: VM):
        host = connected_hosts_with_xo[0]
        vm = imported_vm.clone()
        try:
            networkId = host.management_network()
            hostBr = Network(host, networkId).bridge()

            assert count_of(host, hostBr) == 0, "no OF at init"

            # add OF rule (before starting VM)
            xo_cli('sdnController.addNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
                'allow': 'false',
            })

            # the rule is applied (icmp to 10.0.0.1 is blocked)
            assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
            assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

            # start the VM
            vm.start()

            # wait for XO to see the VM
            vm.wait_for_os_booted()

            # same as previous (after booting VM)
            assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
            assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

            # add OF rule (while running)
            xo_cli('sdnController.addNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.2',
                'direction': 'to',
                'protocol': 'icmp',
                'allow': 'false',
            })

            # both rules are applied
            assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
            assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

            # delete OF rule (while running)
            xo_cli('sdnController.deleteNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
            })

            # second rule only is applied
            assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
            assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

            vm.shutdown(verify=True)

            # same as previous (after VM shutdown)
            assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
            assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

            vm.start()
            vm.wait_for_os_booted()

            # same as previous (after VM restart)
            assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
            assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

            vm.shutdown(verify=True)

            # delete OF rule (while stopped)
            xo_cli('sdnController.deleteNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.2',
                'direction': 'to',
                'protocol': 'icmp',
            })

            # no more rules
            assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
            assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

            assert count_of(host, hostBr) == 0, "no OF at end"
        finally:
            vm.destroy()

            # remove networkRule
            xo_cli('sdnController.deleteNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
            })
            xo_cli('sdnController.deleteNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.2',
                'direction': 'to',
                'protocol': 'icmp',
            })


@pytest.mark.complex_prerequisites
@pytest.mark.small_vm
class TestMigrate:
    def test_vifRule(self, connected_hosts_with_xo: list[Host], hostA2: Host, local_sr_on_hostA2, running_vm: VM):
        hostA1 = connected_hosts_with_xo[0]

        vm = running_vm
        vif = vm.vifs()[0]
        vifId = vif.uuid
        macAddress = vif.mac_address()
        hostBr = vif.network().bridge()

        assert count_of(hostA1, hostBr) == 0, "no OF at init (on hostA1)"
        assert count_of(hostA2, hostBr) == 0, "no OF at init (on hostA2)"

        # no drop before adding the rule
        assert not ofproto_trace_drop(hostA1, hostBr, f"icmp,dl_src={macAddress}")
        assert not ofproto_trace_drop(hostA2, hostBr, f"icmp,dl_src={macAddress}")

        # add OF rule
        logging.info("sdnController.addRule")
        xo_cli('sdnController.addRule', {
            'vifId': vifId,
            'ipRange': '0.0.0.0/0',
            'direction': 'to',
            'protocol': 'icmp',
            'allow': 'false',
        })
        try:
            # drop after adding the rule
            assert ofproto_trace_drop(hostA1, hostBr, f"icmp,dl_src={macAddress}")
            assert ofproto_trace_drop(hostA2, hostBr, f"icmp,dl_src={macAddress}")

            vm.migrate(hostA2, local_sr_on_hostA2)

            # still drop after migrate
            assert ofproto_trace_drop(hostA1, hostBr, f"icmp,dl_src={macAddress}")
            assert ofproto_trace_drop(hostA2, hostBr, f"icmp,dl_src={macAddress}")

        finally:
            logging.info("sdnController.deleteRule")
            xo_cli('sdnController.deleteRule', {
                'vifId': vifId,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'icmp',
            })

        # no more drop after deleting the rule
        assert not ofproto_trace_drop(hostA1, hostBr, f"icmp,dl_src={macAddress}")
        assert not ofproto_trace_drop(hostA2, hostBr, f"icmp,dl_src={macAddress}")

        assert count_of(hostA1, hostBr) == 0, "no OF after deleteRule (on hostA1)"
        assert count_of(hostA2, hostBr) == 0, "no OF after deleteRule (on hostA2)"

    def test_networkRule(self, connected_hosts_with_xo: list[Host], hostA2: Host, local_sr_on_hostA2, running_vm: VM):
        hostA1 = connected_hosts_with_xo[0]

        vm = running_vm
        networkId = hostA1.management_network()
        hostA1Br = Network(hostA1, networkId).bridge()
        hostA2Br = Network(hostA1, networkId).bridge()

        assert count_of(hostA1, hostA1Br) == 0, "no OF at init (on hostA1)"
        assert count_of(hostA2, hostA2Br) == 0, "no OF at init (on hostA2)"

        # no rule
        assert not ofproto_trace_drop(hostA1, hostA1Br, "icmp,nw_dst=10.0.0.1")
        assert not ofproto_trace_drop(hostA2, hostA2Br, "icmp,nw_dst=10.0.0.1")

        # add OF rule
        logging.info("sdnController.addNetworkRule")
        xo_cli('sdnController.addNetworkRule', {
            'networkId': networkId,
            'ipRange': '10.0.0.1',
            'direction': 'to',
            'protocol': 'icmp',
            'allow': 'false',
        })
        try:
            # wait a bit to rules to apply everywhere
            pause()

            # the rule is applied (icmp to 10.0.0.1 is blocked)
            logging.info("check pre-migrate")
            assert ofproto_trace_drop(hostA1, hostA1Br, "icmp,nw_dst=10.0.0.1")
            assert ofproto_trace_drop(hostA2, hostA2Br, "icmp,nw_dst=10.0.0.1")

            vm.migrate(hostA2, local_sr_on_hostA2)

            # the rule is applied (icmp to 10.0.0.1 is blocked)
            logging.info("check post-migrate")
            assert ofproto_trace_drop(hostA1, hostA1Br, "icmp,nw_dst=10.0.0.1")
            assert ofproto_trace_drop(hostA2, hostA2Br, "icmp,nw_dst=10.0.0.1")

        finally:
            logging.info("sdnController.deleteNetworkRule")
            xo_cli('sdnController.deleteNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
            })

        # no more rule
        logging.info("check post-delete")
        assert not ofproto_trace_drop(hostA1, hostA1Br, "icmp,nw_dst=10.0.0.1")
        assert not ofproto_trace_drop(hostA2, hostA2Br, "icmp,nw_dst=10.0.0.1")

        assert count_of(hostA1, hostA1Br) == 0, "no OF at end (on hostA1)"
        assert count_of(hostA2, hostA2Br) == 0, "no OF at end (on hostA2)"

@pytest.mark.complex_prerequisites
@pytest.mark.small_vm
@pytest.mark.parametrize("vlan_device", ["eth1"])
@pytest.mark.parametrize("vlan_tag", [42])
class TestVLAN:
    def test_vifRule(self, connected_hosts_with_xo: list[Host], imported_vm: VM, empty_network: Network,
                     vlan: VLAN, vlan_tag: int, vlan_device: str):
        host = connected_hosts_with_xo[0]
        network = empty_network

        if imported_vm.is_running():
            imported_vm.shutdown(force=True, verify=True)
        vm = imported_vm.clone()

        # get bridge of the network of the tagged PIF
        hostBr = Network(host, vlan.tagged_PIF().network_uuid()).bridge()
        netBr = network.bridge()
        logging.info(f"host bridge for vlan: {hostBr} / {netBr}")

        assert count_of(host, hostBr) == 0, "no OF at start"

        try:
            vif = vm.create_vif(1, network_uuid=network.uuid)
            macAddress = vif.mac_address()
            vm.start()

            # no rule applied
            assert not ofproto_trace_drop(
                host, hostBr,
                f"tcp,tp_dst=81,dl_src={macAddress}",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )
            assert not ofproto_trace_drop(
                host, hostBr,
                f"tcp,tp_dst=82,dl_src={macAddress}",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )

            logging.info("sdnController.addRule1")
            xo_cli('sdnController.addRule', {
                'vifId': vif.uuid,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'tcp',
                'port': 'json:81',
                'allow': 'false',
            })

            vm.wait_for_os_booted()

            # rule applied
            assert ofproto_trace_drop(
                host, hostBr,
                f"tcp,tp_dst=81,dl_src={macAddress}",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )
            assert not ofproto_trace_drop(
                host, hostBr,
                f"tcp,tp_dst=82,dl_src={macAddress}",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )

            logging.info("sdnController.addRule2")
            xo_cli('sdnController.addRule', {
                'vifId': vif.uuid,
                'ipRange': '0.0.0.0/0',
                'direction': 'from',
                'protocol': 'tcp',
                'port': 'json:82',
                'allow': 'false',
            })

            assert ofproto_trace_drop(
                host, hostBr,
                f"tcp,tp_dst=81,dl_src={macAddress}",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )
            assert ofproto_trace_drop(
                host, hostBr,
                f"tcp,tp_src=82,dl_dst={macAddress}",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )

            logging.info("sdnController.deleteRule1")
            xo_cli('sdnController.deleteRule', {
                'vifId': vif.uuid,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'tcp',
                'port': 'json:81',
            })

            assert not ofproto_trace_drop(
                host, hostBr,
                f"tcp,tp_dst=81,dl_src={macAddress}",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )
            assert ofproto_trace_drop(
                host, hostBr,
                f"tcp,tp_src=82,dl_dst={macAddress}",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )

            logging.info("sdnController.deleteRule2")
            xo_cli('sdnController.deleteRule', {
                'vifId': vif.uuid,
                'ipRange': '0.0.0.0/0',
                'direction': 'from',
                'protocol': 'tcp',
                'port': 'json:82',
            })

            # rule not applied
            assert not ofproto_trace_drop(
                host, hostBr,
                f"tcp,tp_dst=81,dl_src={macAddress}",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )
            assert not ofproto_trace_drop(
                host, hostBr,
                f"tcp,tp_src=82,dl_dst={macAddress}",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )

        finally:
            vm.destroy()

        assert count_of(host, hostBr) == 0, "no OF at end"

    def test_networkRule(self, connected_hosts_with_xo: list[Host], imported_vm: VM,
                         empty_network: Network, vlan: VLAN, vlan_tag: int, vlan_device: str):
        host = connected_hosts_with_xo[0]
        network = empty_network
        vm = imported_vm.clone()

        try:
            networkId = network.uuid
            logging.info(f"networkId = {networkId}")

            # get bridge of the network of the tagged PIF
            hostBr = Network(host, vlan.tagged_PIF().network_uuid()).bridge()
            netBr = Network(host, networkId).bridge()
            logging.info(f"host bridge for vlan: {hostBr} / {netBr}")

            # put one vif in the VLAN
            vm.create_vif(1, network_uuid=networkId)

            assert count_of(host, hostBr) == 0, "no OF at init"

            # no rules
            assert not ofproto_trace_drop(
                host, hostBr, "icmp,nw_dst=10.0.0.1",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )

            # add OF rule (before starting VM)
            logging.info("sdnController.addNetworkRule")
            xo_cli('sdnController.addNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
                'allow': 'false',
            })

            # wait a bit
            pause()

            # rule not applied for now (nobody connected to the network)
            # XXX weird, but no OF so seems expected
            logging.info("check pre-start")
            assert not ofproto_trace_drop(
                host, hostBr, "icmp,nw_dst=10.0.0.1",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )

            # start the VM
            vm.start()
            vm.wait_for_os_booted()

            # rule is applied
            logging.info("check post-start")
            assert ofproto_trace_drop(
                host, hostBr, "icmp,nw_dst=10.0.0.1",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )

            vm.shutdown(verify=True)

            # wait a bit
            pause()

            # rule not applied for now (nobody connected to the network)
            # XXX weird, but no OF so seems expected
            logging.info("check post-shutdown")
            assert not ofproto_trace_drop(
                host, hostBr, "icmp,nw_dst=10.0.0.1",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )

        finally:
            vm.destroy()

            # delete networkRule
            logging.info("sdnController.deleteNetworkRule")
            xo_cli('sdnController.deleteNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
            })

        # no rule applied
        assert not ofproto_trace_drop(
            host, hostBr, "icmp,nw_dst=10.0.0.1",
            network_br=netBr,
            vlan_tag=vlan_tag, vlan_device=vlan_device,
        )

        assert count_of(host, hostBr) == 0, "no OF at end"


@pytest.mark.complex_prerequisites
@pytest.mark.small_vm
@pytest.mark.parametrize("tunnel_device", ["eth0"])
class TestTunnel:
    def test_vifRule(self, connected_hosts_with_xo: list[Host], imported_vm: VM,
                     tunnel: Tunnel, tunnel_device: str, tunnel_protocol: str):
        host = connected_hosts_with_xo[0]
        network = Network(host, tunnel.access_PIF().network_uuid())
        vm = imported_vm
        hostBr = network.bridge()

        if vm.is_running():
            vm.shutdown(force=True, verify=True)
        vm = vm.clone()

        assert count_of(host, hostBr) == 0, "no OF at start"

        try:
            vif = vm.create_vif(1, network_uuid=network.uuid)
            macAddress = vif.mac_address()
            vm.start()

            # no rule applied
            assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")

            logging.info("sdnController.addRule")
            xo_cli('sdnController.addRule', {
                'vifId': vif.uuid,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'tcp',
                'port': 'json:81',
                'allow': 'false',
            })

            vm.wait_for_os_booted()

            # rule applied
            assert ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")
        finally:
            vm.destroy()

        assert count_of(host, hostBr) == 0, "no OF at end"

    def test_networkRule(self, connected_hosts_with_xo: list[Host], imported_vm: VM,
                         tunnel: Tunnel, tunnel_device: str, tunnel_protocol: str):
        host = connected_hosts_with_xo[0]
        network = Network(host, tunnel.access_PIF().network_uuid())
        vm = imported_vm.clone()
        hostBr = network.bridge()

        try:
            networkId = network.uuid
            logging.info(f"networkId = {networkId}")

            # put one vif in the Tunnel
            vm.create_vif(1, network_uuid=networkId)

            assert count_of(host, hostBr) == 0, "no OF at init"

            # no rules
            assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")

            # add OF rule (before starting VM)
            logging.info("sdnController.addNetworkRule")
            xo_cli('sdnController.addNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
                'allow': 'false',
            })

            # wait a bit
            pause()

            # rule not applied for now (nobody connected to the network)
            # XXX weird, but no OF so seems expected
            logging.info("check pre-start")
            assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")

            # start the VM
            vm.start()
            vm.wait_for_os_booted()

            # rule is applied
            logging.info("check post-start")
            assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")

            vm.shutdown(verify=True)

            # wait a bit
            pause()

            # rule not applied for now (nobody connected to the network)
            # XXX weird, but no OF so seems expected
            logging.info("check post-shutdown")
            assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")

        finally:
            vm.destroy()

            # delete networkRule
            logging.info("sdnController.deleteNetworkRule")
            xo_cli('sdnController.deleteNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
            })

        # no rule applied
        assert not ofproto_trace_drop(
            host, hostBr, "icmp,nw_dst=10.0.0.1",
        )

        assert count_of(host, hostBr) == 0, "no OF at end"
