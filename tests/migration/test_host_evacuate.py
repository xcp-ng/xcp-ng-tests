import logging
import pytest

from lib.commands import SSHCommandFailed
from lib.common import wait_for
# The pool needs a shared SR to use `host.evacuate`. All three fixtures below are needed.
from tests.storage.nfs.conftest import vm_on_nfs_sr, nfs_sr, nfs_device_config

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.3 (for most tests)
# - hostA2: Second member of the pool.
# From --vm parameter
# - A VM to import
# From data.py or --sr-device-config parameter: configuration to create a new NFS SR.
# From --second-network parameter (for most tests)
# - second_network: A 2nd physical network of the pool with PIF plugged and configured with an IP on all hosts
#   Must NOT be the management interface.
#   Must NOT be the network used to access the NFS SR.
#   This network will be disconnected at some point during the tests

def _host_evacuate_test(source_host, dest_host, network_uuid, vm, expect_error=False, error=""):
    vm.start(on=source_host.uuid)
    vm.wait_for_os_booted()
    args = {'host': source_host.uuid}
    if network_uuid is not None:
        args['network-uuid'] = network_uuid

    try:
        if not expect_error:
            logging.info(f"Evacuate host {source_host}")
        else:
            logging.info(f"Attempt evacuating host {source_host}. This should fail.")
        source_host.xe('host-evacuate', args)
        wait_for(lambda: vm.all_vdis_on_host(dest_host), "Wait for all VDIs on destination host")
        wait_for(lambda: vm.is_running_on_host(dest_host), "Wait for VM to be running on destination host")
        vm.wait_for_os_booted()
        assert not expect_error, "host-evacuate should have raised: %s" % error
    except SSHCommandFailed as e:
        if not (expect_error and e.stdout.find(error) > -1):
            raise
        logging.info(f"Evacuate failed with the expected error: {error}.")
    finally:
        vm.shutdown(verify=True)

def _save_ip_configuration_mode(host, pif_uuid):
    args = {'uuid': pif_uuid}
    ipv6 = (host.xe('pif-param-get', {'uuid': pif_uuid, 'param-name': 'primary-address-type'}) == "IPv6")
    keys = [
        ('IPv6', 'IPv6'), ('DNS', 'DNS'), ('gateway', 'IPv6-gateway'), ('mode', 'IPv6-configuration-mode')
    ] if ipv6 else [
        ('IP', 'IP'), ('DNS', 'DNS'), ('gateway', 'gateway'), ('netmask', 'netmask'), ('mode', 'IP-configuration-mode')
    ]
    for key, param in keys:
        res = host.xe('pif-param-get', {'uuid': pif_uuid, 'param-name': param})
        if res != "":
            args[key] = res

    return args

@pytest.mark.small_vm # what we test here is that evacuate works, the goal is not to test with various VMs
class TestHostEvacuate:
    def test_host_evacuate(self, host, hostA2, vm_on_nfs_sr):
        _host_evacuate_test(host, hostA2, None, vm_on_nfs_sr)

@pytest.mark.complex_prerequisites # requires a special network setup.
@pytest.mark.small_vm # what we test here is the network-uuid option, the goal is not to test with various VMs
@pytest.mark.usefixtures("host_at_least_8_3")
class TestHostEvacuateWithNetwork:
    def test_host_evacuate_with_network(self, host, hostA2, second_network, vm_on_nfs_sr):
        _host_evacuate_test(host, hostA2, second_network, vm_on_nfs_sr)

    def test_host_evacuate_with_network_no_ip(self, host, hostA2, second_network, vm_on_nfs_sr):
        pif_uuid = host.xe('pif-list', {'host-uuid': hostA2.uuid, 'network-uuid': second_network}, minimal=True)
        ipv6 = (host.xe('pif-param-get', {'uuid': pif_uuid, 'param-name': 'primary-address-type'}) == "IPv6")
        reconfigure_method = 'pif-reconfigure-ipv6' if ipv6 else 'pif-reconfigure-ip'
        args = _save_ip_configuration_mode(hostA2, pif_uuid)
        logging.info(f"Reconfigure PIF {pif_uuid}: remove its IP")
        host.xe(reconfigure_method, {'uuid': pif_uuid, 'mode': 'none'})
        try:
            no_ip_error = 'The specified interface cannot be used because it has no IP address'
            _host_evacuate_test(host, hostA2, second_network, vm_on_nfs_sr, True, no_ip_error)
        finally:
            logging.info(f"Restore the configuration of PIF {pif_uuid}")
            host.xe(reconfigure_method, args)

    def test_host_evacuate_with_network_not_attached(self, host, hostA2, second_network, vm_on_nfs_sr):
        pif_uuid = host.xe('pif-list', {'host-uuid': hostA2.uuid, 'network-uuid': second_network}, minimal=True)
        logging.info(f"Unplug PIF {pif_uuid}")
        host.xe('pif-unplug', {'uuid': pif_uuid})
        try:
            not_attached_error = \
                'The operation you requested cannot be performed because the specified PIF is currently unplugged'
            _host_evacuate_test(host, hostA2, second_network, vm_on_nfs_sr, True, not_attached_error)
        finally:
            logging.info(f"Re-plug PIF {pif_uuid}")
            host.xe('pif-plug', {'uuid': pif_uuid})

    def test_host_evacuate_with_network_not_present(self, host, hostA2, second_network, vm_on_nfs_sr):
        pif_uuid = host.xe('pif-list', {'host-uuid': hostA2.uuid, 'network-uuid': second_network}, minimal=True)
        ipv6 = (host.xe('pif-param-get', {'uuid': pif_uuid, 'param-name': 'primary-address-type'}) == "IPv6")
        reconfigure_method = 'pif-reconfigure-ipv6' if ipv6 else 'pif-reconfigure-ip'
        args = _save_ip_configuration_mode(hostA2, pif_uuid)
        logging.info(f"Forget PIF {pif_uuid}")
        host.xe('pif-forget', {'uuid': pif_uuid})
        try:
            not_present_error = 'This host has no PIF on the given network'
            _host_evacuate_test(host, hostA2, second_network, vm_on_nfs_sr, True, not_present_error)
        finally:
            host.xe('pif-scan', {'host-uuid': hostA2.uuid})
            pif_uuid = host.xe('pif-list', {'host-uuid': hostA2.uuid, 'network-uuid': second_network}, minimal=True)
            args['uuid'] = pif_uuid
            logging.info(f"Re-add and plug PIF {pif_uuid}")
            host.xe(reconfigure_method, args)
            host.xe('pif-plug', {'uuid': pif_uuid})
