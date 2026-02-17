from __future__ import annotations

import pytest

import logging

from data import HOST_FREE_NICS
from lib.common import PackageManagerEnum
from lib.host import Host
from lib.network import Network
from lib.vm import VM

from typing import Generator

@pytest.fixture(scope='package')
def host_no_sdn_controller(host: Host) -> None:
    """ An XCP-ng with no SDN controller. """
    if host.xe('sdn-controller-list', minimal=True):
        pytest.fail("This test requires an XCP-ng with no SDN controller")

# a clone of imported_vm in which we've added tcpdump
# not to be used by tests directly
@pytest.fixture(scope='module')
def vm_with_tcpdump_scope_module(imported_vm: VM):
    logging.info("Preparing VM with tcpdump installed")
    vm = imported_vm.clone()
    vm.start()
    vm.wait_for_vm_running_and_ssh_up()

    # install tcpdump
    pkg_mgr = vm.detect_package_manager()
    if pkg_mgr == PackageManagerEnum.APK:
        vm.ssh("apk add tcpdump")
    elif pkg_mgr == PackageManagerEnum.APT_GET:
        vm.ssh("apt-get install tcpdump")
    elif pkg_mgr == PackageManagerEnum.RPM:
        # XXX assume yum for now
        vm.ssh("yum install tcpdump")
    else:
        pytest.fail("Package manager '%s' not supported" % pkg_mgr)

    vm.shutdown(verify=True)
    yield vm
    vm.destroy()

@pytest.fixture(scope='function')
def vm_with_tcpdump_scope_function(vm_with_tcpdump_scope_module: VM):
    vm = vm_with_tcpdump_scope_module.clone()
    yield vm
    vm.destroy()

@pytest.fixture(scope='module')
def empty_network(host: Host) -> Generator[Network, None, None]:
    net = host.create_network(label="empty_network for tests")
    yield net
    net.destroy()

@pytest.fixture(scope='function')
def bond_lacp(host: Host, empty_network: Network):
    if len(HOST_FREE_NICS) < 2:
        pytest.fail("This fixture needs at least 2 free NICs")

    pifs = []
    logging.info(f"bond: resolve PIFs on {host.hostname_or_ip} using \
        {[(pif.network_uuid(), pif.param_get('device')) for pif in host.pifs()]}")
    for name in HOST_FREE_NICS[0:2]:
        [pif] = host.pifs(device=name)
        pifs.append(pif)

    bond = host.create_bond(empty_network, pifs, mode="lacp")
    yield bond
    bond.destroy()

@pytest.fixture(scope='function')
def bond_activebackup(host: Host, empty_network: Network):
    if len(HOST_FREE_NICS) < 2:
        pytest.fail("This fixture needs at least 2 free NICs")

    pifs = []
    logging.info(f"bond: resolve PIFs on {host.hostname_or_ip} using \
        {[(pif.network_uuid(), pif.param_get('device')) for pif in host.pifs()]}")
    for name in HOST_FREE_NICS[0:2]:
        [pif] = host.pifs(device=name)
        pifs.append(pif)

    bond = host.create_bond(empty_network, pifs, mode="active-backup")
    yield bond
    bond.destroy()

@pytest.fixture(scope='function')
def bond_balanceslb(host: Host, empty_network: Network):
    if len(HOST_FREE_NICS) < 2:
        pytest.fail("This fixture needs at least 2 free NICs")

    pifs = []
    logging.info(f"bond: resolve PIFs on {host.hostname_or_ip} using \
        {[(pif.network_uuid(), pif.param_get('device')) for pif in host.pifs()]}")
    for name in HOST_FREE_NICS[0:2]:
        [pif] = host.pifs(device=name)
        pifs.append(pif)

    bond = host.create_bond(empty_network, pifs, mode="balance-slb")
    yield bond
    bond.destroy()
