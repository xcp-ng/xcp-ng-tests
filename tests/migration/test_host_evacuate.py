import pytest

from subprocess import CalledProcessError
from lib.common import wait_for, wait_for_not

# The pool needs a shared SR to use `host.evacuate`
from tests.storage.nfs.conftest import vm_on_nfs_sr, nfs_sr, nfs_device_config

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.2 (+ updates) with an additional unused disk for the SR.
# - hostA2: Second member of the pool. Can have any local SR. No need to specify it on CLI.
# From --vm parameter
# - A VM to import
# From --second-network parameter
# - second_network: A 2nd network of the pool, NOT the management interface, with PIF plugged on all hosts

pytestmark = pytest.mark.default_vm("mini-linux-x86_64-bios")


def _host_evacuate_test(
    source_host, dest_host, network_uuid, vm, expect_error=False, error=""
):
    source_name = source_host.xe(
        "host-param-get", {"uuid": source_host.uuid, "param-name": "name-label"}
    )
    vm.start(on=source_name)
    vm.wait_for_os_booted()
    args = {"host": source_host.uuid}
    if network_uuid is not None:
        args["network-uuid"] = network_uuid

    try:
        source_host.xe("host-evacuate", args)
        wait_for(
            lambda: vm.all_vdis_on_host(dest_host),
            "Wait for all VDIs on destination host",
        )
        wait_for(
            lambda: vm.is_running_on_host(dest_host),
            "Wait for VM to be running on destination host",
        )
        vm.wait_for_os_booted()
        assert not expect_error, "host-evacuate should have raised: %s" % error
    except CalledProcessError as e:
        if not (expect_error and e.output.find(error) > -1):
            raise
    finally:
        vm.shutdown(verify=True)


def _save_ip_configuration_mode(host, pif_uuid):
    args = {"uuid": pif_uuid}
    ipv6 = (
        host.xe(
            "pif-param-get", {"uuid": pif_uuid, "param-name": "primary-address-type"}
        )
        == "IPv6"
    )
    keys = (
        [
            ("IPv6", "IPv6"),
            ("DNS", "DNS"),
            ("gateway", "IPv6-gateway"),
            ("mode", "IPv6-configuration-mode"),
        ]
        if ipv6
        else [
            ("IP", "IP"),
            ("DNS", "DNS"),
            ("gateway", "gateway"),
            ("netmask", "netmask"),
            ("mode", "IP-configuration-mode"),
        ]
    )
    for key, param in keys:
        args[key] = host.xe("pif-param-get", {"uuid": pif_uuid, "param-name": param})

    return args


def test_host_evacuate(host, hostA2, vm_on_nfs_sr):
    _host_evacuate_test(host, hostA2, None, vm_on_nfs_sr)


def test_host_evacuate_with_network(host, hostA2, second_network, vm_on_nfs_sr):
    _host_evacuate_test(host, hostA2, second_network, vm_on_nfs_sr)


def test_host_evacuate_with_network_no_ip(host, hostA2, second_network, vm_on_nfs_sr):
    pif_uuid = host.xe(
        "pif-list",
        {"host-uuid": host.uuid, "network-uuid": second_network},
        minimal=True,
    )
    ipv6 = (
        host.xe(
            "pif-param-get", {"uuid": pif_uuid, "param-name": "primary-address-type"}
        )
        == "IPv6"
    )
    reconfigure_method = "pif-reconfigure-ipv6" if ipv6 else "pif-reconfigure-ip"
    args = _save_ip_configuration_mode(host, pif_uuid)
    host.xe(reconfigure_method, {"uuid": pif_uuid, "mode": "none"})
    try:
        no_ip_error = (
            b"The specified interface cannot be used because it has no IP address"
        )
        _host_evacuate_test(
            host, hostA2, second_network, vm_on_nfs_sr, True, no_ip_error
        )
    finally:
        host.xe(reconfigure_method, args)


def test_host_evacuate_with_network_not_attached(
    host, hostA2, second_network, vm_on_nfs_sr
):
    pif_uuid = host.xe(
        "pif-list",
        {"host-uuid": host.uuid, "network-uuid": second_network},
        minimal=True,
    )
    host.xe("pif-unplug", {"uuid": pif_uuid})
    try:
        not_attached_error = b"The operation you requested cannot be performed because the specified PIF is currently unplugged"
        _host_evacuate_test(
            host, hostA2, second_network, vm_on_nfs_sr, True, not_attached_error
        )
    finally:
        host.xe("pif-plug", {"uuid": pif_uuid})


def test_host_evacuate_with_network_not_present(
    host, hostA2, second_network, vm_on_nfs_sr
):
    pif_uuid = host.xe(
        "pif-list",
        {"host-uuid": host.uuid, "network-uuid": second_network},
        minimal=True,
    )
    ipv6 = (
        host.xe(
            "pif-param-get", {"uuid": pif_uuid, "param-name": "primary-address-type"}
        )
        == "IPv6"
    )
    reconfigure_method = "pif-reconfigure-ipv6" if ipv6 else "pif-reconfigure-ip"
    args = _save_ip_configuration_mode(host, pif_uuid)
    host.xe("pif-forget", {"uuid": pif_uuid})
    try:
        not_present_error = b"This host has no PIF on the given network"
        _host_evacuate_test(
            host, hostA2, second_network, vm_on_nfs_sr, True, not_present_error
        )
    finally:
        host.xe("pif-scan", {"host-uuid": host.uuid})
        pif_uuid = host.xe(
            "pif-list",
            {"host-uuid": host.uuid, "network-uuid": second_network},
            minimal=True,
        )
        args["uuid"] = pif_uuid
        host.xe(reconfigure_method, args)
        host.xe("pif-plug", {"uuid": pif_uuid})
