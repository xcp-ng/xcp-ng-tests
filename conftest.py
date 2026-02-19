from __future__ import annotations

import pytest

import itertools
import logging
import os
import tempfile

import git
from packaging import version

import lib.config as global_config
from lib import pxe
from lib.common import (
    callable_marker,
    DiskDevName,
    HostAddress,
    is_uuid,
    prefix_object_name,
    setup_formatted_and_mounted_disk,
    shortened_nodeid,
    teardown_formatted_and_mounted_disk,
    vm_image,
    wait_for,
)
from lib.netutil import is_ipv6
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR
from lib.vm import VM, vm_cache_key_from_def
from lib.xo import xo_cli

# Import package-scoped fixtures. Although we need to define them in a separate file so that we can
# then import them in individual packages to fix the buggy package scope handling by pytest, we also
# need to import them in the global conftest.py so that they are recognized as fixtures.
from pkgfixtures import formatted_and_mounted_ext4_disk, sr_disk_wiped

from typing import Dict, Generator, Iterable

# Do we cache VMs?
try:
    from data import CACHE_IMPORTED_VM
except ImportError:
    CACHE_IMPORTED_VM = False
assert CACHE_IMPORTED_VM in [True, False]

# pytest hooks

def pytest_addoption(parser):
    parser.addoption(
        "--nest",
        action="store",
        default=None,
        help="XCP-ng or XS master of pool to use for nesting hosts under test",
    )
    parser.addoption(
        "--hosts",
        action="append",
        default=[],
        help="XCP-ng or XS list of master hosts (comma-separated)",
    )
    parser.addoption(
        "--vm",
        action="append",
        default=[],
        help="VM key or OVA URL for tests that require only one VM",
    )
    parser.addoption(
        "--second-network",
        action="store",
        default=None,
        help="UUID of second network in the A pool, NOT the management network"
    )
    parser.addoption(
        "--ignore-ssh-banner",
        action="store_true",
        default=False,
        help="Ignore SSH banners when SSH commands are executed"
    )
    parser.addoption(
        "--ssh-output-max-lines",
        action="store",
        default=20,
        help="Max lines to output in a ssh log (0 if no limit)"
    )
    parser.addoption(
        "--disks",
        action="append",
        default=[],
        help="HOST:DISKS to authorize for use by tests. "
             "DISKS is a possibly-empty comma-separated list. "
             "No mention of a given host authorizes use of all its disks."
    )
    parser.addoption(
        "--image-format",
        action="append",
        default=[],
        help="Format of VDI to execute tests on."
        "Example: vhd,qcow2"
    )

def pytest_configure(config):
    global_config.ignore_ssh_banner = config.getoption('--ignore-ssh-banner')
    global_config.ssh_output_max_lines = int(config.getoption('--ssh-output-max-lines'))

def pytest_generate_tests(metafunc):
    if "vm_ref" in metafunc.fixturenames:
        vms = metafunc.config.getoption("vm")
        if not vms:
            vms = [None] # no --vm parameter does not mean skip the test, for us, it means use the default
        metafunc.parametrize("vm_ref", vms, indirect=True, scope="module")

    if "image_format" in metafunc.fixturenames:
        image_format = metafunc.config.getoption("image_format")
        if len(image_format) == 0:
            image_format = ["vhd"] # Not giving image-format will default to doing tests on vhd
        metafunc.parametrize("image_format", image_format, scope="session")

def pytest_collection_modifyitems(items, config):
    # Automatically mark tests based on fixtures they require.
    # Check pytest.ini or pytest --markers for marker descriptions.

    markable_fixtures = [
        'uefi_vm',
        'unix_vm',
        'windows_vm',
        'hostA2',
        'hostB1',
        'unused_512B_disks',
        'unused_4k_disks',
    ]

    for item in items:
        fixturenames = getattr(item, 'fixturenames', ())
        for fixturename in markable_fixtures:
            if fixturename in fixturenames:
                item.add_marker(fixturename)

        if 'vm_ref' not in fixturenames:
            item.add_marker('no_vm')

        if item.get_closest_marker('multi_vms'):
            # multi_vms implies small_vm
            item.add_marker('small_vm')

# BEGIN make test results visible from fixtures
# from https://docs.pytest.org/en/latest/example/simple.html#making-test-result-information-available-in-fixtures

# FIXME we may have to move this into lib/ if fixtures in sub-packages
# want to make use of this feature

PHASE_REPORT_KEY = pytest.StashKey[Dict[str, pytest.CollectReport]]()
@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    # execute all other hooks to obtain the report object
    rep = yield

    # store test results for each phase of a call, which can
    # be "setup", "call", "teardown"
    item.stash.setdefault(PHASE_REPORT_KEY, {})[rep.when] = rep

    return rep

# END make test results visible from fixtures


# fixtures

@pytest.fixture(scope='session')
def hosts(pytestconfig) -> Generator[list[Host]]:
    nested_list = []

    def setup_host(hostname_or_ip, *, config=None):
        host_vm = None
        if hostname_or_ip.startswith("cache://"):
            if config is None:
                raise RuntimeError("setup_host: a cache:// host requires --nest")
            nest_hostname = config.getoption("nest")
            if not nest_hostname:
                pytest.fail("--hosts=cache://... requires --nest parameter")
            nest = Pool(nest_hostname).master

            protocol, rest = hostname_or_ip.split(":", 1)
            host_vm = nest.import_vm(f"clone:{rest}", nest.main_sr_uuid(),
                                     use_cache=True)
            nested_list.append(host_vm)

            vif = host_vm.vifs()[0]
            mac_address = vif.param_get('MAC')
            logging.info("Nested host has MAC %s", mac_address)

            host_vm.start()
            wait_for(host_vm.is_running, "Wait for nested host VM running")

            # catch host-vm IP address
            wait_for(lambda: pxe.arp_addresses_for(mac_address),
                     "Wait for DHCP server to see nested host in ARP tables",
                     timeout_secs=10 * 60)
            ips = pxe.arp_addresses_for(mac_address)
            logging.info("Nested host has IPs %s", ips)
            assert len(ips) == 1
            host_vm.ip = ips[0]

            wait_for(lambda: not os.system(f"nc -zw5 {host_vm.ip} 22"),
                     "Wait for ssh up on nested host", retry_delay_secs=5)

            hostname_or_ip = host_vm.ip

        pool = Pool(hostname_or_ip)
        h = pool.master
        return h

    def cleanup_hosts():
        for vm in nested_list:
            logging.info("Destroying nested host VM %s", vm.uuid)
            vm.destroy(verify=True)

    # a list of master hosts, each from a different pool
    hosts_args = pytestconfig.getoption("hosts")
    hosts_split = [hostlist.split(',') for hostlist in hosts_args]
    hostname_list = list(itertools.chain(*hosts_split))

    try:
        host_list = [setup_host(hostname_or_ip, config=pytestconfig)
                     for hostname_or_ip in hostname_list]
    except Exception:
        cleanup_hosts()
        raise

    if not host_list:
        pytest.fail("This test requires at least one --hosts parameter")
    yield host_list

    cleanup_hosts()

@pytest.fixture(scope='session')
def pools_hosts_by_name_or_ip(hosts: list[Host]) -> Generator[dict[HostAddress, Host]]:
    """All hosts of all pools, each indexed by their hostname_or_ip."""
    yield {host.hostname_or_ip: host
           for pool_master in hosts
           for host in pool_master.pool.hosts
           }

@pytest.fixture(scope='session')
def registered_xo_cli():
    # The fixture is not responsible for establishing the connection.
    # We just check that xo-cli is currently registered
    try:
        xo_cli('server.getAll')
    except Exception as e:
        raise Exception(f"Check for registered xo_cli failed: {e}")

@pytest.fixture(scope='session')
def hosts_with_xo(hosts, registered_xo_cli):
    for h in hosts:
        logging.info(">>> Connect host %s" % h)
        if not h.skip_xo_config:
            h.xo_server_add(h.user, h.password)
        else:
            h.xo_get_server_id(store=True)
        wait_for(h.xo_server_connected, timeout_secs=10)
    yield hosts
    # teardown
    for h in hosts:
        if not h.skip_xo_config:
            logging.info("<<< Disconnect host %s" % h)
            h.xo_server_remove()

@pytest.fixture(scope='session')
def hostA1(hosts):
    """ Master of first pool (pool A). """
    yield hosts[0]

@pytest.fixture(scope='session')
def host(hostA1):
    """ Convenience fixture for hostA1. """
    yield hostA1

@pytest.fixture(scope='session')
def hostA2(hostA1):
    """ Second host of pool A. """
    assert len(hostA1.pool.hosts) > 1, "A second host in first pool is required"
    _hostA2 = hostA1.pool.hosts[1]
    logging.info(">>> hostA2 present: %s" % _hostA2)
    yield _hostA2

@pytest.fixture(scope='session')
def hostB1(hosts):
    """ Master of second pool (pool B). """
    assert len(hosts) > 1, "A second pool is required"
    assert hosts[0].pool.uuid != hosts[1].pool.uuid
    _hostB1 = hosts[1]
    logging.info(">>> hostB1 present: %s" % _hostB1)
    yield _hostB1

@pytest.fixture(scope='session')
def host_at_least_8_3(host):
    version_str = "8.3"
    if not host.xcp_version >= version.parse(version_str):
        pytest.skip(f"This test requires an XCP-ng >= {version_str} host")

@pytest.fixture(scope='session')
def host_less_than_8_3(host):
    version_str = "8.3"
    if not host.xcp_version < version.parse(version_str):
        pytest.skip(f"This test requires an XCP-ng < {version_str} host")

@pytest.fixture(scope='session')
def host_with_hsts(host):
    host.enable_hsts_header()
    yield host
    host.disable_hsts_header()

@pytest.fixture(scope='function')
def xfail_on_xcpng_8_3(host, request):
    """ Test that is relevant but expected to fail in current state of XCP-ng 8.3. """
    if host.xcp_version >= version.parse("8.3"):
        request.node.add_marker(pytest.mark.xfail)

@pytest.fixture(scope='session')
def host_no_ipv6(host):
    if is_ipv6(host.hostname_or_ip):
        pytest.skip(f"This test requires an IPv4 XCP-ng")

@pytest.fixture(scope="session")
def shared_sr(host):
    sr = host.pool.first_shared_sr()
    assert sr, "No shared SR available on hosts"
    logging.info(">> Shared SR on host present: {} of type {}".format(sr.uuid, sr.get_type()))
    yield sr

@pytest.fixture(scope='session')
def local_sr_on_hostA1(hostA1):
    """ A local SR on the pool's master. """
    srs = hostA1.local_vm_srs()
    assert len(srs) > 0, "a local SR is required on the pool's master"
    # use the first local SR found
    sr = srs[0]
    logging.info(">> local SR on hostA1 present: {} of type {}".format(sr.uuid, sr.get_type()))
    yield sr

@pytest.fixture(scope='session')
def local_sr_on_hostA2(hostA2):
    """ A local SR on the pool's second host. """
    srs = hostA2.local_vm_srs()
    assert len(srs) > 0, "a local SR is required on the pool's second host"
    # use the first local SR found
    sr = srs[0]
    logging.info(">> local SR on hostA2 present: {} of type {}".format(sr.uuid, sr.get_type()))
    yield sr

@pytest.fixture(scope='session')
def local_sr_on_hostB1(hostB1):
    """ A local SR on the second pool's master. """
    srs = hostB1.local_vm_srs()
    assert len(srs) > 0, "a local SR is required on the second pool's master"
    # use the first local SR found
    sr = srs[0]
    logging.info(">> local SR on hostB1 present: {} of type {}".format(sr.uuid, sr.get_type()))
    yield sr

@pytest.fixture(scope='session')
def disks(pytestconfig, pools_hosts_by_name_or_ip: dict[HostAddress, Host]
          ) -> dict[Host, list[Host.BlockDeviceInfo]]:
    """Dict identifying names of all disks for on all hosts of first pool."""
    def _parse_disk_option(option_text: str) -> tuple[HostAddress, list[DiskDevName]]:
        parsed = option_text.split(sep=":", maxsplit=1)
        assert len(parsed) == 2, f"--disks option {option_text!r} is not <host>:<disk>[,<disk>]*"
        host_address, disks_string = parsed
        devices = disks_string.split(',') if disks_string else []
        return host_address, devices

    cli_disks = dict(_parse_disk_option(option_text)
                     for option_text in pytestconfig.getoption("disks"))

    def _host_disks(host: Host, hosts_cli_disks: list[DiskDevName] | None) -> Iterable[Host.BlockDeviceInfo]:
        """Filter host disks according to list from `--cli` if given."""
        host_disks = host.disks()
        # no disk specified = allow all
        if hosts_cli_disks is None:
            yield from host_disks
            return
        # check all disks in --disks=host:... exist
        for cli_disk in hosts_cli_disks:
            for disk in host_disks:
                if disk['name'] == cli_disk:
                    yield disk
                    break # names are unique, don't expect another one
            else:
                raise Exception(f"no {cli_disk!r} disk on host {host.hostname_or_ip}, "
                                f"has {','.join(disk['name'] for disk in host_disks)}")

    ret = {host: list(_host_disks(host, cli_disks.get(host.hostname_or_ip)))
           for host in pools_hosts_by_name_or_ip.values()
           }
    logging.debug("disks collected: %s", {host.hostname_or_ip: value for host, value in ret.items()})
    return ret

@pytest.fixture(scope='session')
def unused_512B_disks(disks: dict[Host, list[Host.BlockDeviceInfo]]
                      ) -> dict[Host, list[Host.BlockDeviceInfo]]:
    """Dict identifying names of all 512-bytes-blocks disks for on all hosts of first pool."""
    ret = {host: [disk for disk in host_disks
                  if disk["log-sec"] == "512" and host.disk_is_available(disk["name"])]
           for host, host_disks in disks.items()
           }
    logging.debug("available disks collected: %s", {host.hostname_or_ip: value for host, value in ret.items()})
    return ret

@pytest.fixture(scope='session')
def unused_4k_disks(disks: dict[Host, list[Host.BlockDeviceInfo]]
                    ) -> dict[Host, list[Host.BlockDeviceInfo]]:
    """Dict identifying names of all 4K-blocks disks for on all hosts of first pool."""
    ret = {host: [disk for disk in host_disks
                  if disk["log-sec"] == "4096" and host.disk_is_available(disk["name"])]
           for host, host_disks in disks.items()
           }
    logging.debug("available 4k disks collected: %s", {host.hostname_or_ip: value for host, value in ret.items()})
    return ret

@pytest.fixture(scope='session')
def pool_with_unused_512B_disk(host: Host, unused_512B_disks: dict[Host, list[Host.BlockDeviceInfo]]) -> Pool:
    """Returns the first pool, ensuring all hosts have at least one unused 512-bytes-blocks disk."""
    for h in host.pool.hosts:
        assert h in unused_512B_disks
        assert unused_512B_disks[h], f"host {h} does not have any unused 512B-block disk"
    return host.pool

@pytest.fixture(scope='module')
def vm_ref(request):
    ref = request.param

    if ref is None:
        # get default VM from test if there's one
        marker = request.node.get_closest_marker("default_vm")
        if marker is not None:
            ref = marker.args[0]
            logging.info(">> No VM specified on CLI. Using default: %s.", ref)
        else:
            # global default
            logging.info(">> No VM specified on CLI, and no default found in test definition. Using global default.")
            ref = 'mini-linux-x86_64-bios'

    if is_uuid(ref):
        return ref
    elif ref.startswith('http'):
        return ref
    else:
        return vm_image(ref)

@pytest.fixture(scope="module")
def imported_vm(host, vm_ref):
    if is_uuid(vm_ref):
        vm_orig = VM(vm_ref, host)
        name = vm_orig.name()
        logging.info(">> Reuse VM %s (%s) on host %s" % (vm_ref, name, host))
    else:
        vm_orig = host.import_vm(vm_ref, host.main_sr_uuid(), use_cache=CACHE_IMPORTED_VM)

    if CACHE_IMPORTED_VM:
        # Clone the VM before running tests, so that the original VM remains untouched
        logging.info(">> Clone cached VM before running tests")
        vm = vm_orig.clone()
        # Remove the description, which may contain a cache identifier
        vm.param_set('name-description', "")
    else:
        vm = vm_orig

    yield vm
    # teardown
    if not is_uuid(vm_ref):
        logging.info("<< Destroy VM")
        vm.destroy(verify=True)

@pytest.fixture(scope="session")
def tests_git_revision():
    """
    Get the git revision string for this tests repo.

    Use of this fixture means impacted tests cannot run unless all
    modifications are commited.
    """
    test_repo = git.Repo(".")
    assert not test_repo.is_dirty(), "test repo must not be dirty"
    yield test_repo.head.commit.hexsha

@pytest.fixture(scope="function")
def create_vms(request, host, tests_git_revision):
    """
    Returns list of VM objects created from `vm_definitions` marker.

    `vm_definitions` marker test author to specify one or more VMs, by
    giving for each VM one `dict`, or a callable taking fixtures as
    arguments and returning such a `dict`.

    Mandatory keys:
    - `name`: name of the VM to create (str)
    - `template`: name (or UUID) of template to use (str)

    Optional keys: see example below

    Example:
    -------
    > @pytest.mark.vm_definitions(
    >     dict(name="vm1", template="Other install media"),
    >     dict(name="vm2",
    >          template="CentOS 7",
    >          params=(
    >              dict(param_name="memory-static-max", value="4GiB"),
    >              dict(param_name="HVM-boot-params", key="order", value="dcn"),
    >          ),
    >          vdis=[dict(name="vm 2 system disk",
    >                     size="100GiB",
    >                     device="xvda",
    >                     userdevice="0",
    >                     )],
    >          cd_vbd=dict(device="xvdd", userdevice="3"),
    >          vifs=(dict(index=0, network_name=NETWORKS["MGMT"]),
    >                dict(index=1, network_uuid=NETWORKS["MYNET_UUID"]),
    >          ),
    >     ))
    > def test_foo(create_vms):
    >    ...

    Example:
    -------
    > @pytest.mark.dependency(depends=["test_foo"])
    > @pytest.mark.vm_definitions(dict(name="vm1", image_test="test_foo", image_vm="vm2"))
    > def test_bar(create_vms):
    >    ...

    """
    marker = request.node.get_closest_marker("vm_definitions")
    if marker is None:
        raise Exception("No vm_definitions marker specified.")

    vm_defs = []
    for vm_def in marker.args:
        vm_def = callable_marker(vm_def, request)
        assert "name" in vm_def
        assert "template" in vm_def or "image_test" in vm_def
        if "template" in vm_def:
            assert "image_test" not in vm_def
            # FIXME should check optional vdis contents
        # FIXME should check for extra args
        vm_defs.append(vm_def)

    vms = []
    vdis = []
    vbds = []
    try:
        for vm_def in vm_defs:
            if "template" in vm_def:
                _create_vm(request, vm_def, host, vms, vdis, vbds)
            elif "image_test" in vm_def:
                _vm_from_cache(request, vm_def, host, vms, tests_git_revision)
        yield vms

        # request.node is an "item" because this fixture has "function" scope
        report = request.node.stash.get(PHASE_REPORT_KEY, None)
        if report is None:
            # user interruption during setup
            logging.warning("test setup result not available: not exporting VMs")
        elif report["setup"].failed:
            logging.warning("setting up a test failed or skipped: not exporting VMs")
        elif ("call" not in report) or report["call"].failed:
            logging.warning("executing test failed or skipped: not exporting VMs")
        else:
            # record this state
            for vm_def, vm in zip(vm_defs, vms):
                nodeid = shortened_nodeid(request.node.nodeid)
                vm.save_to_cache(f"{nodeid}-{vm_def['name']}-{tests_git_revision}")

    except Exception:
        logging.error("exception caught...")
        raise

    finally:
        for vbd in vbds:
            logging.info("<< Destroy VBD %s", vbd.uuid)
            vbd.destroy()
        for vdi in vdis:
            logging.info("<< Destroy VDI %s", vdi.uuid)
            vdi.destroy()
        for vm in vms:
            logging.info("<< Destroy VM %s", vm.uuid)
            vm.destroy(verify=True)

def _vm_name(request, vm_def):
    return f"{vm_def['name']} in {request.node.nodeid}"

def _create_vm(request, vm_def, host, vms, vdis, vbds):
    vm_name = _vm_name(request, vm_def)
    vm_template = vm_def["template"]

    logging.info("Installing VM %r from template %r", vm_name, vm_template)

    vm = host.vm_from_template(vm_name, vm_template)

    # VM is now created, make sure we clean it up on any subsequent failure
    vms.append(vm)

    if "vdis" in vm_def:
        for vdi_def in vm_def["vdis"]:
            sr = SR(host.main_sr_uuid(), host.pool)
            vdi = sr.create_vdi(vdi_def["name"], vdi_def["size"])
            vdis.append(vdi)
            # connect to VM
            vbd = vm.create_vbd(vdi_def["device"], vdi.uuid)
            vbds.append(vbd)
            vbd.param_set(param_name="userdevice", value=vdi_def["userdevice"])

    if "cd_vbd" in vm_def:
        vm.create_cd_vbd(**vm_def["cd_vbd"])

    if "vifs" in vm_def:
        for vif_def in vm_def["vifs"]:
            vm.create_vif(vif_def["index"],
                          network_uuid=vif_def.get("network_uuid", None),
                          network_name=vif_def.get("network_name", None))

    if "params" in vm_def:
        for param_def in vm_def["params"]:
            logging.info("Setting param %s", param_def)
            vm.param_set(**param_def)

def _vm_from_cache(request, vm_def, host, vms, tests_hexsha):
    base_vm = host.cached_vm(vm_cache_key_from_def(vm_def, request.node.nodeid, tests_hexsha),
                             sr_uuid=host.main_sr_uuid())
    if base_vm is None:
        raise RuntimeError("No cache found")

    # Clone the VM before running tests, so that the original VM remains untouched
    logging.info("Cloning VM from cache")
    vm = base_vm.clone(name=prefix_object_name(_vm_name(request, vm_def)))
    # Remove the description, which may contain a cache identifier
    vm.param_set('name-description', "")

    vms.append(vm)

@pytest.fixture(scope="module")
def started_vm(imported_vm):
    vm = imported_vm
    # may be already running if we skipped the import to use an existing VM
    if not vm.is_running():
        vm.start()
    wait_for(vm.is_running, '> Wait for VM running')
    wait_for(vm.try_get_and_store_ip, "> Wait for VM IP", timeout_secs=5 * 60)
    return vm
    # no teardown

@pytest.fixture(scope="module")
def running_vm(started_vm):
    vm = started_vm
    wait_for(vm.is_ssh_up, "> Wait for VM SSH up")
    return vm

@pytest.fixture(scope='module')
def unix_vm(imported_vm):
    vm = imported_vm
    if vm.is_windows:
        pytest.skip("This test is only compatible with unix VMs.")
    yield vm

@pytest.fixture(scope="module")
def running_unix_vm(unix_vm, running_vm):
    return running_vm
    # no teardown

@pytest.fixture(scope='module')
def windows_vm(imported_vm):
    vm = imported_vm
    if not vm.is_windows:
        pytest.skip("This test is only compatible with Windows VMs.")
    yield vm

@pytest.fixture(scope='module')
def uefi_vm(imported_vm):
    vm = imported_vm
    if not vm.is_uefi:
        pytest.skip('This test requires an UEFI VM')
    yield vm

@pytest.fixture(scope='session')
def additional_repos(request, hosts):
    if request.param is None:
        yield []
        return

    repo_file = '/etc/yum.repos.d/xcp-ng-additional-tester.repo'
    url_list = request.param.split(',')

    with tempfile.NamedTemporaryFile('wt') as temp:
        for id, url in enumerate(url_list):
            temp.write("""[xcp-ng-tester-{}]
name=XCP-ng Tester {}
baseurl={}
enabled=1
gpgcheck=0
""".format(id, id, url))
        temp.flush()

        for host in hosts:
            for host_ in host.pool.hosts:
                host_.scp(temp.name, repo_file)

    yield url_list

    for host in hosts:
        for host_ in host.pool.hosts:
            host_.ssh(['rm', '-f', repo_file])

@pytest.fixture(scope='session')
def second_network(pytestconfig, host):
    network_uuids = pytestconfig.getoption("second_network")
    if len(network_uuids) != 1:
        pytest.fail("This test requires exactly one --second-network parameter!")
    network_uuid = network_uuids[0]
    pif_uuid = host.xe('pif-list', {'host-uuid': host.uuid, 'network-uuid': network_uuid}, minimal=True)
    if not pif_uuid:
        pytest.fail("The provided --second-network UUID doesn't exist or doesn't have a PIF on master host")
    ipv6 = (host.xe('pif-param-get', {'uuid': pif_uuid, 'param-name': 'primary-address-type'}) == "IPv6")
    ip = host.xe('pif-param-get', {'uuid': pif_uuid, 'param-name': 'IPv6' if ipv6 else 'IP'})
    if not ip:
        pytest.fail("The provided --second-network has a PIF but no IP")
    if network_uuid == host.management_network():
        pytest.fail("--second-network must NOT be the management network")
    return network_uuid

@pytest.fixture(scope='module')
def nfs_iso_device_config():
    return global_config.sr_device_config("NFS_ISO_DEVICE_CONFIG", required=['location'])

@pytest.fixture(scope='module')
def cifs_iso_device_config():
    return global_config.sr_device_config("CIFS_ISO_DEVICE_CONFIG")

@pytest.fixture(scope='module')
def nfs_iso_sr(host, nfs_iso_device_config):
    """ A NFS ISO SR. """
    sr = host.sr_create('iso', "ISO-NFS-SR-test", nfs_iso_device_config, shared=True, verify=True)
    yield sr
    # teardown
    sr.forget()

@pytest.fixture(scope='function')
def exit_on_fistpoint(host):
    from lib.fistpoint import FistPoint
    logging.info(">> Enabling exit on fistpoint")
    FistPoint.enable_exit_on_fistpoint(host)
    yield
    logging.info("<< Disabling exit on fistpoint")
    FistPoint.disable_exit_on_fistpoint(host)

@pytest.fixture(scope='module')
def cifs_iso_sr(host, cifs_iso_device_config):
    """ A Samba/CIFS SR. """
    sr = host.sr_create('iso', "ISO-CIFS-SR-test", cifs_iso_device_config, shared=True, verify=True)
    yield sr
    # teardown
    sr.forget()

pytest_plugins = ["pytest_features"]
