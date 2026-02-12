from __future__ import annotations

import pytest

from lib.common import safe_split
from lib.host import Host
from lib.vm import VM
from lib.xo import xo_cli

def ofctl_dumpflows(host: Host, br: str) -> list[str]:
    flows = host.ssh_with_result(f"ovs-ofctl -O OpenFlow11 dump-flows '{br}' | grep -F cookie=")
    if flows.returncode == 0:
        return safe_split(flows.stdout, sep='\n')[0:-1]
    else:
        pytest.fail(f"ofctl_dumpflows: {flows.stdout}")

def count_of(host: Host, br: str, with_base=False):
    base = 0 if with_base else -1
    return len(ofctl_dumpflows(host, br)) + base

# XXX xo-cli (on test host) should be configured to see the host
# XXX particular configuration
@pytest.mark.complex_prerequisites
@pytest.mark.small_vm
class TestSimple:
    def test_vifRule(self, connected_hosts_with_xo: list[Host], imported_vm: VM):
        host = connected_hosts_with_xo[0]
        vm = imported_vm.clone()
        try:
            vifId = vm.vifs()[0].uuid
            hostBr = "xenbr0" # XXX hardcoded: get info from host

            assert count_of(host, hostBr) == 0, "no OF at init"

            # add OF rule (before starting VM)
            xo_cli('sdnController.addRule', {
                'vifId': vifId,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'tcp',
                'port': 'json:80',
                'allow': 'true',
            })

            assert count_of(host, hostBr) == 0, "before starting VM, still no OF"

            # start the VM
            vm.start()

            assert count_of(host, hostBr) == 0, "after just starting VM, still no OF"

            # wait for XO to see the VM
            vm.wait_for_os_booted()

            assert count_of(host, hostBr) == 1, "after XO see the VM, 1 OF"

            # add OF rule (while running)
            xo_cli('sdnController.addRule', {
                'vifId': vifId,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'tcp',
                'port': 'json:81',
                'allow': 'true',
            })

            assert count_of(host, hostBr) == 2, "addRule while running VM, 2 OF"

            # delete OF rule (while running)
            xo_cli('sdnController.deleteRule', {
                'vifId': vifId,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'tcp',
                'port': 'json:80',
            })

            assert count_of(host, hostBr) == 1, "removeRule, while VM running, 1 OF"

            vm.shutdown(verify=True)
            assert count_of(host, hostBr) == 0, "after shutdown VM, no OF"

            # delete OF rule (while stopped)
            xo_cli('sdnController.deleteRule', {
                'vifId': vifId,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'tcp',
                'port': 'json:81',
            })

            assert count_of(host, hostBr) == 0, "after shutdown VM, still no OF"

            vm.start()
            vm.wait_for_os_booted()

            assert count_of(host, hostBr) == 0, "after restarting VM, still no OF"

        finally:
            vm.destroy()

    def test_networkRule(self, connected_hosts_with_xo: list[Host], imported_vm: VM):
        host = connected_hosts_with_xo[0]
        vm = imported_vm.clone()
        try:
            networkId = host.management_network()
            hostBr = "xenbr0" # XXX hardcoded: get info from host

            n_prev = 0
            n_curr = count_of(host, hostBr)
            assert n_curr == 0, "no OF at init"

            # add OF rule (before starting VM)
            xo_cli('sdnController.addNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
                'allow': 'true',
            })

            n_prev = n_curr
            n_curr = count_of(host, hostBr)
            assert n_curr > 0, "OF should be added"

            # start the VM
            vm.start()

            # wait for XO to see the VM
            vm.wait_for_os_booted()

            n_prev = n_curr
            n_curr = count_of(host, hostBr)
            assert n_curr == n_prev, "OF should be the same (after start)"

            # add OF rule (while running)
            xo_cli('sdnController.addNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.2',
                'direction': 'to',
                'protocol': 'icmp',
                'allow': 'true',
            })

            n_prev = n_curr
            n_curr = count_of(host, hostBr)
            assert n_curr > n_prev, "OF should have increase"

            # delete OF rule (while running)
            xo_cli('sdnController.deleteNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
            })

            n_prev = n_curr
            n_curr = count_of(host, hostBr)
            assert n_curr < n_prev, "OF should have decrease"

            vm.shutdown(verify=True)

            n_prev = n_curr
            n_curr = count_of(host, hostBr)
            assert n_curr == n_prev, "OF should be the same (after shutdown)"

            # delete OF rule (while stopped)
            xo_cli('sdnController.deleteNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.2',
                'direction': 'to',
                'protocol': 'icmp',
            })

            n_prev = n_curr
            n_curr = count_of(host, hostBr)
            assert n_curr == 0, "no OF at end"
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


# XXX xo-cli (on test host) should be configured to see the host
# XXX particular configuration
@pytest.mark.complex_prerequisites
@pytest.mark.small_vm
class TestMigrate:
    def test_vifRule(self, connected_hosts_with_xo: list[Host], hostA2: Host, local_sr_on_hostA2, running_vm: VM):
        hostA1 = connected_hosts_with_xo[0]

        vm = running_vm
        vifId = vm.vifs()[0].uuid
        hostBr = "xenbr0" # XXX hardcoded: get info from host

        assert count_of(hostA1, hostBr) == 0, "no OF at init (on hostA1)"
        assert count_of(hostA2, hostBr) == 0, "no OF at init (on hostA2)"

        # add OF rule
        xo_cli('sdnController.addRule', {
            'vifId': vifId,
            'ipRange': '0.0.0.0/0',
            'direction': 'to',
            'protocol': 'tcp',
            'port': 'json:80',
            'allow': 'true',
        })
        try:
            assert count_of(hostA1, hostBr) == 1, "1 OF after addRule (on hostA1)"
            assert count_of(hostA2, hostBr) == 1, "1 OF after addRule (on hostA2)"

            vm.migrate(hostA2, local_sr_on_hostA2)

            assert count_of(hostA1, hostBr) == 1, "1 OF after migrate (on hostA1)"
            assert count_of(hostA2, hostBr) == 1, "1 OF after migrate (on hostA2)"

        finally:
            xo_cli('sdnController.deleteRule', {
                'vifId': vifId,
                'ipRange': '0.0.0.0/0',
                'direction': 'to',
                'protocol': 'tcp',
                'port': 'json:80',
            })

        assert count_of(hostA1, hostBr) == 0, "no OF after deleteRule (on hostA1)"
        assert count_of(hostA2, hostBr) == 0, "no OF after deleteRule (on hostA2)"

    def test_networkRule(self, connected_hosts_with_xo: list[Host], hostA2: Host, local_sr_on_hostA2, running_vm: VM):
        hostA1 = connected_hosts_with_xo[0]

        vm = running_vm
        networkId = hostA1.management_network()
        hostBr = "xenbr0" # XXX hardcoded: get info from host

        assert count_of(hostA1, hostBr) == 0, "no OF at init (on hostA1)"
        assert count_of(hostA2, hostBr) == 0, "no OF at init (on hostA2)"

        # add OF rule
        xo_cli('sdnController.addNetworkRule', {
            'networkId': networkId,
            'ipRange': '10.0.0.1',
            'direction': 'to',
            'protocol': 'icmp',
            'allow': 'true',
        })
        try:
            assert count_of(hostA1, hostBr) > 0, "OF after addNetworkRule (on hostA1)"
            assert count_of(hostA2, hostBr) > 0, "OF after addNetworkRule (on hostA2)"

            vm.migrate(hostA2, local_sr_on_hostA2)

            assert count_of(hostA1, hostBr) > 0, "OF after addNetworkRule (on hostA1)"
            assert count_of(hostA2, hostBr) > 0, "OF after addNetworkRule (on hostA2)"

        finally:
            xo_cli('sdnController.deleteNetworkRule', {
                'networkId': networkId,
                'ipRange': '10.0.0.1',
                'direction': 'to',
                'protocol': 'icmp',
            })

        assert count_of(hostA1, hostBr) == 0, "1 OF after migrate (on hostA1)"
        assert count_of(hostA2, hostBr) == 0, "1 OF after migrate (on hostA2)"
