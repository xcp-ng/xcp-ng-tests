from __future__ import annotations

import pytest

import logging
import os

from lib.common import Defer, safe_split, wait_for, wait_for_not
from lib.host import Host
from lib.network import Network
from lib.sr import SR
from lib.tunnel import Tunnel
from lib.vlan import VLAN
from lib.vm import VM
from lib.xo import xo_cli, xo_object_exists

from typing import Callable

# Requirements:
# xo-cli (on the host running the test) is expected to be usable
# From --hosts parameter:
# - host(A1): first XCP-ng host (no traffic rules should be already present on the host)
# From --vm parameter
# - A VM to import

# Special requirements for some tests:
# - TestVLAN needs at least 1 free NICs (see HOST_FREE_NICS in data.py)
# - TestMigrate needs second XCP-ng host in the same pool
# - TestTunnel will create encrypted tunnel (and only one could be created at a time)

cache_ovs_vsctl_bridge_to_parent: dict[str, str] = {}

def ovs_vsctl_bridge_to_parent(host: Host, br: str) -> str:
    key = f"test({os.environ.get('PYTEST_CURRENT_TEST')})-host({host.uuid})-br({br})"

    if key not in cache_ovs_vsctl_bridge_to_parent:
        cache_ovs_vsctl_bridge_to_parent[key] = host.ssh(f"ovs-vsctl br-to-parent {br}")

    return cache_ovs_vsctl_bridge_to_parent[key]

def ofctl_dumpflows(host: Host, br: str) -> list[str]:
    """
    Get the list of dump-flows installed for the bridge {br}
    """
    br = ovs_vsctl_bridge_to_parent(host, br)
    return host.ssh(
        f"ovs-ofctl -O OpenFlow11 dump-flows '{br}' | grep -F cookie=",
    ).splitlines()

def count_of(host: Host, br: str) -> int:
    """
    Return the number of OF flows in the bridge (excluding the default one)
    """
    return len(ofctl_dumpflows(host, br)) - 1
def ofproto_trace_drop_in_port(
    host: Host, br: str, flow: str, in_port: str,
    vlan_tag: int | None, vlan_device: str | None,
) -> bool:
    """
    Run ovs-appctl ofproto/trace program to check OpenFlow rules processing
    on a specific port of a bridge.
    """
    if vlan_device == in_port:
        flow = f"in_port={in_port},vlan_vid={vlan_tag},{flow}"
    else:
        flow = f"in_port={in_port},{flow}"

    logging.debug(f"ofproto/trace port='{in_port}'")
    result = host.ssh(f"ovs-appctl ofproto/trace {br} {flow}")
    return result.endswith("Datapath actions: drop")

def ofproto_trace_drop(
    host: Host, br: str, flow: str,
    network_br: str | None = None,
    vlan_tag: int | None = None, vlan_device: str | None = None,
) -> bool:
    """
    Run ovs-appctl ofproto/trace program to check OpenFlow rules processing
    on all ports of a bridge.
    """
    logging.info(f"Checking OpenFlow state on {br}: '{flow}'")

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

    br = ovs_vsctl_bridge_to_parent(host, br)
    logging.debug(f"ofproto/trace: dumping flows: {br}")
    host.ssh(f"ovs-ofctl -O OpenFlow11 dump-flows {br}")

    return all([
        ofproto_trace_drop_in_port(
            host, br, flow, port,
            vlan_tag, vlan_device,
        )
        for port in ports
    ])

def sync_sdnController_action(
    host: Host,
    action: str,
    args: dict[str, str] = {},
) -> str:
    def log_date(host: Host) -> str:
        # %y : file modification date, example: "2026-08-24 15:15:58.982053917 +0200"
        return host.ssh("stat -c %y /var/log/sdn-controller-plugin.log", check=False)

    hosts = {}

    # on each host, get the number of Host.call_plugin calls
    for h in host.pool.hosts:
        hosts[h.name()] = log_date(h)

    # wait for XO to sync
    if 'vifId' in args:
        wait_for(
            lambda: xo_object_exists(args['vifId']),
            "Wait for XO to sync VIF",
        )
    if 'networkId' in args:
        wait_for(
            lambda: xo_object_exists(args['networkId']),
            "Wait for XO to sync Network",
        )

    # run the sdnController action
    logging.info(f"sdnController.{action}")
    ret = xo_cli(f"sdnController.{action}", args)

    # as XO is contacting only involved hosts, so some hosts in the pool might *not* be contacted at all
    # wait max 5 sec, and discard TimeoutError exception
    try:
        for h in host.pool.hosts:
            wait_for(
                lambda: log_date(h) != hosts[h.name()],
                timeout_secs=5,
            )
    except TimeoutError:
        pass

    return ret

def ofctl_replaceflows(host: Host, br: str, flows: list[str] | None) -> list[str]:
    """
    Get/set the list of OpenFlow rules installed for the bridge {br}
    """
    br = ovs_vsctl_bridge_to_parent(host, br)

    oflows = host.ssh(
        f"ovs-ofctl -O OpenFlow11 dump-flows '{br}' | grep -F cookie=",
    ).splitlines()

    if flows is not None:
        # ensure that allow-all rule is present
        flows.append("cookie=0x0, table=0, priority=0 actions=NORMAL")

        tmpfile = host.ssh('mktemp -t ofctl_replaceflows.XXXXXXXX')
        host.create_file(tmpfile, "\n".join(flows))
        host.ssh(f'ovs-ofctl -O OpenFlow11 replace-flows {br} {tmpfile}; rm -f {tmpfile}')

    return oflows

def set_clean_rules_state(defer: Defer, host: Host, bridge: str) -> None:
    """
    Ensure we start with no rules on the bridge of the host.
    """
    # high level for network rules
    network_uuid = host.xe('network-list', {
        'params': 'uuid',
        'bridge': bridge,
    }, minimal=True)
    network = Network(host, network_uuid)
    of_rules = network.param_get(
        'other-config',
        'xo:sdn-controller:of-rules',
        accept_unknown_key=True,
    )
    if of_rules is not None:
        network.param_remove('other-config', 'xo:sdn-controller:of-rules')
        defer(lambda: network.param_set('other-config', of_rules, 'xo:sdn-controller:of-rules'))
        logging.warn(f"Network '{network_uuid}' has of-rules present (will be cleared for the test): {of_rules}")

    # low level OpenFlow rules check
    flows = ofctl_replaceflows(host, bridge, [])
    defer(lambda: ofctl_replaceflows(host, bridge, flows))

    n = len(flows)
    if n != 1:
        # we reseted the flows, but give a warning that it wasn't clean
        logging.warn(f"OpenFlow rules were already present on the bridge '{bridge}' on host '{host.name()}': "
                     f"found {n} rules (see `ovs-ofctl dump-flows {bridge}`).")

def assert_clean_openflow_state(host: Host, bridge: str) -> None:
    """
    Assert that the list of OpenFlow rules is empty on the bridge on host.
    """
    flows = ofctl_replaceflows(host, bridge, None)

    n = len(flows)
    if n != 1:
        pytest.fail(f"OpenFlow rules are still present on the bridge '{bridge}' on host '{host.name()}': "
                    f"found {n} rules (see `ovs-ofctl dump-flows {bridge}`).")

def xo_vm_power_state(vm: VM, power_state: str) -> Callable[[], bool]:
    """
    Return a function that return if the VM is seen by XO in the given power_state.
    """
    def vm_power_state() -> bool:
        lst = xo_cli('list-objects', {
            'type': 'VM',
            'uuid': vm.uuid,
        }, use_json=True)
        assert isinstance(lst, list)

        if len(lst) != 1:
            return False

        xovm = lst[0]
        assert isinstance(xovm, dict)
        return (power_state == xovm.get('power_state', '?'))

    return vm_power_state

def has_running_vm_on_network(host: Host, network_uuid: str) -> bool:
    # running VMs on the host
    vms = safe_split(host.xe('vm-list', {
        'params': 'uuid',
        'power-state': 'running',
        'resident-on': host.uuid,
    }, minimal=True))

    # check VIFs of running VMs
    for uuid in vms:
        vifs = safe_split(host.xe('vif-list', {
            'params': 'uuid',
            'vm-uuid': uuid,
            'network-uuid': network_uuid,
        }, minimal=True))

        if len(vifs) != 0:
            return True

    return False

@pytest.mark.small_vm
class TestSimple:
    def test_vifRule(self, hosts_with_traffic_rules: list[Host], imported_vm: VM, defer: Defer) -> None:
        host = hosts_with_traffic_rules[0]
        vm = imported_vm.clone()
        defer(lambda: vm.destroy())

        vif = vm.vifs()[0]
        vifId = vif.uuid
        macAddress = vif.mac_address()
        hostBr = vif.network().bridge()

        set_clean_rules_state(defer, host, hostBr)

        # add OF rule (before starting VM)
        sync_sdnController_action(host, 'addRule', {
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

        # start the VM and wait for XO to see the VM
        vm.start()
        vm.wait_for_os_booted()

        # right after VM booted
        assert ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=80,dl_src={macAddress}")
        assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")

        # add OF rule (while running)
        sync_sdnController_action(host, 'addRule', {
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
        sync_sdnController_action(host, 'deleteRule', {
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

        vm.shutdown(verify=True)

        # delete OF rule (while stopped)
        sync_sdnController_action(host, 'deleteRule', {
            'vifId': vifId,
            'ipRange': '0.0.0.0/0',
            'direction': 'to',
            'protocol': 'tcp',
            'port': 'json:81',
        })

        # no more traffic blocked
        assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=80,dl_src={macAddress}")
        assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")

        assert_clean_openflow_state(host, hostBr)

    def test_networkRule(self, hosts_with_traffic_rules: list[Host], imported_vm: VM, defer: Defer) -> None:
        host = hosts_with_traffic_rules[0]
        vm = imported_vm.clone()
        defer(lambda: vm.destroy())
        networkId = host.management_network()
        hostBr = Network(host, networkId).bridge()

        set_clean_rules_state(defer, host, hostBr)

        # add OF rule (before starting VM)
        sync_sdnController_action(host, 'addNetworkRule', {
            'networkId': networkId,
            'ipRange': '10.0.0.1',
            'direction': 'to',
            'protocol': 'icmp',
            'allow': 'false',
        })
        defer(
            lambda:
                sync_sdnController_action(host, 'deleteNetworkRule', {
                    'networkId': networkId,
                    'ipRange': '10.0.0.1',
                    'direction': 'to',
                    'protocol': 'icmp',
                })
        )

        # the rule is not applied if there is no interface in the network at the time
        if not has_running_vm_on_network(host, networkId):
            assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
        else:
            assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
        assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

        # start the VM
        vm.start()

        # wait for XO to see the VM
        vm.wait_for_os_booted()

        # the rule is applied (after booting VM)
        assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
        assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

        # add OF rule (while running)
        sync_sdnController_action(host, 'addNetworkRule', {
            'networkId': networkId,
            'ipRange': '10.0.0.2',
            'direction': 'to',
            'protocol': 'icmp',
            'allow': 'false',
        })
        defer(
            lambda:
                sync_sdnController_action(host, 'deleteNetworkRule', {
                    'networkId': networkId,
                    'ipRange': '10.0.0.2',
                    'direction': 'to',
                    'protocol': 'icmp',
                })
        )

        # both rules are applied
        assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
        assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

        # delete OF rule (while running)
        sync_sdnController_action(host, 'deleteNetworkRule', {
            'networkId': networkId,
            'ipRange': '10.0.0.1',
            'direction': 'to',
            'protocol': 'icmp',
        })

        # second rule only is applied
        assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
        assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

        # restart the VM
        vm.shutdown(verify=True)
        vm.start()
        vm.wait_for_os_booted()

        # same as previous (after VM restart)
        assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
        assert ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

        vm.shutdown(verify=True)

        # delete OF rule (while stopped)
        sync_sdnController_action(host, 'deleteNetworkRule', {
            'networkId': networkId,
            'ipRange': '10.0.0.2',
            'direction': 'to',
            'protocol': 'icmp',
        })

        # no more rules
        assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")
        assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.2")

        assert_clean_openflow_state(host, hostBr)


@pytest.mark.small_vm
class TestMigrate:
    def test_vifRule(
        self,
        hosts_with_traffic_rules: list[Host],
        hostA2: Host,
        local_sr_on_hostA2: SR,
        imported_vm: VM,
        defer: Defer,
    ) -> None:
        hostA1 = hosts_with_traffic_rules[0]

        vm = imported_vm.clone()
        defer(lambda: vm.destroy())
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()

        vif = vm.vifs()[0]
        vifId = vif.uuid
        macAddress = vif.mac_address()
        hostBr = vif.network().bridge()

        set_clean_rules_state(defer, hostA1, hostBr)
        set_clean_rules_state(defer, hostA2, hostBr)

        # no drop before adding the rule
        assert not ofproto_trace_drop(hostA1, hostBr, f"icmp,dl_src={macAddress}")
        assert not ofproto_trace_drop(hostA2, hostBr, f"icmp,dl_src={macAddress}")

        # add OF rule
        sync_sdnController_action(hostA1, 'addRule', {
            'vifId': vifId,
            'ipRange': '0.0.0.0/0',
            'direction': 'to',
            'protocol': 'icmp',
            'allow': 'false',
        })
        try:
            # drop after adding the rule
            assert ofproto_trace_drop(hostA1, hostBr, f"icmp,dl_src={macAddress}")

            vm.migrate(hostA2, local_sr_on_hostA2)

            wait_for(
                lambda: ofproto_trace_drop(hostA2, hostBr, f"icmp,dl_src={macAddress}"),
                msg="Wait for still dropping after migrate",
                timeout_secs=30,
            )

        finally:
            sync_sdnController_action(hostA1, 'deleteRule', {
                'vifId': vifId,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'icmp',
            })

        # no more drop after deleting the rule
        assert not ofproto_trace_drop(hostA1, hostBr, f"icmp,dl_src={macAddress}")
        assert not ofproto_trace_drop(hostA2, hostBr, f"icmp,dl_src={macAddress}")

        assert_clean_openflow_state(hostA1, hostBr)
        assert_clean_openflow_state(hostA2, hostBr)

    def test_networkRule(
        self,
        hosts_with_traffic_rules: list[Host],
        hostA2: Host,
        local_sr_on_hostA1: SR,
        local_sr_on_hostA2: SR,
        imported_vm: VM,
        defer: Defer,
    ) -> None:
        hostA1 = hosts_with_traffic_rules[0]

        vm = imported_vm.clone()
        defer(lambda: vm.destroy())
        vm.start(on=hostA1.name()) # the test is assymetric, start with the VM on know host
        vm.wait_for_vm_running_and_ssh_up()

        networkId = hostA1.management_network()
        hostA1Br = Network(hostA1, networkId).bridge()
        hostA2Br = Network(hostA2, networkId).bridge()

        set_clean_rules_state(defer, hostA1, hostA1Br)
        set_clean_rules_state(defer, hostA2, hostA2Br)

        # no rule
        assert not ofproto_trace_drop(hostA1, hostA1Br, "icmp,nw_dst=10.0.0.1")
        assert not ofproto_trace_drop(hostA2, hostA2Br, "icmp,nw_dst=10.0.0.1")

        # add OF rule
        sync_sdnController_action(hostA1, 'addNetworkRule', {
            'networkId': networkId,
            'ipRange': '10.0.0.1',
            'direction': 'to',
            'protocol': 'icmp',
            'allow': 'false',
        })
        try:
            logging.info("check pre-migrate")
            wait_for(
                lambda: ofproto_trace_drop(hostA1, hostA1Br, "icmp,nw_dst=10.0.0.1"),
                msg="Wait for the rule to applied on hostA1",
                timeout_secs=30,
            )

            vm.migrate(hostA2, local_sr_on_hostA2)

            logging.info("check post-migrate")
            wait_for(
                lambda: ofproto_trace_drop(hostA2, hostA2Br, "icmp,nw_dst=10.0.0.1"),
                msg="Wait for the rule to applied on hostA2",
                timeout_secs=30,
            )

        finally:
            sync_sdnController_action(hostA1, 'deleteNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
            })

        # no more rule
        logging.info("check post-delete")
        assert not ofproto_trace_drop(hostA1, hostA1Br, "icmp,nw_dst=10.0.0.1")
        assert not ofproto_trace_drop(hostA2, hostA2Br, "icmp,nw_dst=10.0.0.1")

        assert_clean_openflow_state(hostA1, hostA1Br)
        assert_clean_openflow_state(hostA2, hostA2Br)


@pytest.mark.complex_prerequisites
@pytest.mark.small_vm
class TestVLAN:
    def test_vifRule(self, hosts_with_traffic_rules: list[Host], imported_vm: VM, empty_network: Network,
                     vlan: VLAN, defer: Defer) -> None:
        host = hosts_with_traffic_rules[0]
        network = empty_network

        vlan_tag = vlan.tag()
        vlan_device = vlan.untagged_pif().device()

        vm = imported_vm.clone()
        defer(lambda: vm.destroy())

        # get bridge of the network of the tagged PIF
        hostBr = Network(host, vlan.tagged_pif().network_uuid()).bridge()
        netBr = network.bridge()
        logging.info(f"host bridge for vlan: {hostBr} / {netBr}")

        set_clean_rules_state(defer, host, hostBr)

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

        sync_sdnController_action(host, 'addRule', {
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

        sync_sdnController_action(host, 'addRule', {
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

        sync_sdnController_action(host, 'deleteRule', {
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

        sync_sdnController_action(host, 'deleteRule', {
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

        assert_clean_openflow_state(host, hostBr)

    def test_networkRule(self, hosts_with_traffic_rules: list[Host], imported_vm: VM,
                         empty_network: Network, vlan: VLAN, defer: Defer) -> None:
        host = hosts_with_traffic_rules[0]
        network = empty_network
        vm = imported_vm.clone()
        defer(lambda: vm.destroy())
        vlan_tag = vlan.tag()
        vlan_device = vlan.untagged_pif().device()

        networkId = network.uuid
        logging.info(f"networkId = {networkId}")

        try:
            # get bridge of the network of the tagged PIF
            hostBr = Network(host, vlan.tagged_pif().network_uuid()).bridge()
            netBr = Network(host, networkId).bridge()
            logging.info(f"host bridge for vlan: {hostBr} / {netBr}")

            # put one vif in the VLAN
            vm.create_vif(1, network_uuid=networkId)

            set_clean_rules_state(defer, host, hostBr)

            # no rules
            assert not ofproto_trace_drop(
                host, hostBr, "icmp,nw_dst=10.0.0.1",
                network_br=netBr,
                vlan_tag=vlan_tag, vlan_device=vlan_device,
            )

            # add OF rule (before starting VM)
            sync_sdnController_action(host, 'addNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
                'allow': 'false',
            })

            # XXX weird, but no OF so seems expected
            logging.info("check pre-start")
            wait_for_not(
                lambda: ofproto_trace_drop(
                    host, hostBr, "icmp,nw_dst=10.0.0.1",
                    network_br=netBr,
                    vlan_tag=vlan_tag, vlan_device=vlan_device,
                ),
                msg="Wait for the rule to not apply for now (nobody connected to the network)",
                timeout_secs=30,
            )

            # start the VM
            vm.start()
            wait_for(xo_vm_power_state(vm, "Running"), msg="Waiting for XO to see VM is running")

            # rule is applied
            logging.info("check post-start")
            wait_for(
                lambda: ofproto_trace_drop(
                    host, hostBr, "icmp,nw_dst=10.0.0.1",
                    network_br=netBr,
                    vlan_tag=vlan_tag, vlan_device=vlan_device,
                ),
                msg="Wait for the rule to be applied",
                timeout_secs=30,
            )

            vm.shutdown(verify=True, force=True)
            wait_for(xo_vm_power_state(vm, "Halted"), msg="Waiting for XO to see VM is halted")

            # XXX weird, but no OF so seems expected
            logging.info("check post-shutdown")
            wait_for_not(
                lambda: ofproto_trace_drop(
                    host, hostBr, "icmp,nw_dst=10.0.0.1",
                    network_br=netBr,
                    vlan_tag=vlan_tag, vlan_device=vlan_device,
                ),
                msg="Wait for the rule to not apply for now (nobody connected to the network)",
                timeout_secs=30,
            )

        finally:
            # delete networkRule
            sync_sdnController_action(host, 'deleteNetworkRule', {
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

        assert_clean_openflow_state(host, hostBr)


@pytest.mark.small_vm
class TestTunnel:
    def test_vifRule(self, hosts_with_traffic_rules: list[Host], imported_vm: VM,
                     tunnel: Tunnel, tunnel_protocol: str, defer: Defer) -> None:
        host = hosts_with_traffic_rules[0]
        network = Network(host, tunnel.access_pif().network_uuid())
        hostBr = network.bridge()

        vm = imported_vm.clone()
        defer(lambda: vm.destroy())

        set_clean_rules_state(defer, host, hostBr)

        vif = vm.create_vif(1, network_uuid=network.uuid)
        macAddress = vif.mac_address()
        vm.start()

        # no rule applied
        assert not ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}")

        sync_sdnController_action(host, 'addRule', {
            'vifId': vif.uuid,
            'ipRange': '0.0.0.0/0',
            'direction': 'to',
            'protocol': 'tcp',
            'port': 'json:81',
            'allow': 'false',
        })

        # rule applied
        wait_for(
            lambda: ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}"),
            msg="Wait for rule to apply",
            timeout_secs=30,
        )

        sync_sdnController_action(host, 'deleteRule', {
            'vifId': vif.uuid,
            'ipRange': '0.0.0.0/0',
            'direction': 'to',
            'protocol': 'tcp',
            'port': 'json:81',
        })

        wait_for_not(
            lambda: ofproto_trace_drop(host, hostBr, f"tcp,tp_dst=81,dl_src={macAddress}"),
            msg="Wait for rule to not apply anymore",
            timeout_secs=30,
        )

        assert_clean_openflow_state(host, hostBr)

    def test_networkRule(self, hosts_with_traffic_rules: list[Host], imported_vm: VM,
                         tunnel: Tunnel, tunnel_protocol: str, defer: Defer) -> None:
        host = hosts_with_traffic_rules[0]
        network = Network(host, tunnel.access_pif().network_uuid())
        vm = imported_vm.clone()
        defer(lambda: vm.destroy())
        hostBr = network.bridge()

        networkId = network.uuid
        logging.info(f"networkId = {networkId}")

        try:
            # put one vif in the Tunnel
            vm.create_vif(1, network_uuid=networkId)

            set_clean_rules_state(defer, host, hostBr)

            # no rules
            assert not ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1")

            # add OF rule (before starting VM)
            sync_sdnController_action(host, 'addNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
                'allow': 'false',
            })

            # XXX weird, but no OF so seems expected
            logging.info("check pre-start")
            wait_for_not(
                lambda: ofproto_trace_drop(
                    host, hostBr, "icmp,nw_dst=10.0.0.1",
                ),
                msg="Wait for the rule to not apply for now (nobody connected to the network)",
                timeout_secs=30,
            )

            # start the VM
            vm.start()
            wait_for(xo_vm_power_state(vm, "Running"), msg="Waiting for XO to see VM is running")

            # rule is applied
            logging.info("check post-start")
            wait_for(
                lambda: ofproto_trace_drop(host, hostBr, "icmp,nw_dst=10.0.0.1"),
                msg="Wait for the rule to be applied",
                timeout_secs=30,
            )

            vm.shutdown(verify=True, force=True)
            wait_for(xo_vm_power_state(vm, "Halted"), msg="Waiting for XO to see VM is halted")

            # XXX weird, but no OF so seems expected
            logging.info("check post-shutdown")
            wait_for_not(
                lambda: ofproto_trace_drop(
                    host, hostBr, "icmp,nw_dst=10.0.0.1",
                ),
                msg="Wait for the rule to not apply for now (nobody connected to the network)",
                timeout_secs=30,
            )

        finally:
            # delete networkRule
            sync_sdnController_action(host, 'deleteNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
            })

        # no rule applied
        assert not ofproto_trace_drop(
            host, hostBr, "icmp,nw_dst=10.0.0.1",
        )

        assert_clean_openflow_state(host, hostBr)
