import itertools
import logging
import pytest, _pytest
import tempfile

from packaging import version

import lib.config as global_config

from lib.common import callable_marker, shortened_nodeid
from lib.common import wait_for, vm_image, is_uuid
from lib.common import setup_formatted_and_mounted_disk, teardown_formatted_and_mounted_disk
from lib.netutil import is_ipv6
from lib.pool import Pool
from lib.sr import SR
from lib.vm import VM, xva_name_from_def
from lib.xo import xo_cli

# Import package-scoped fixtures. Although we need to define them in a separate file so that we can
# then import them in individual packages to fix the buggy package scope handling by pytest, we also
# need to import them in the global conftest.py so that they are recognized as fixtures.
from pkgfixtures import formatted_and_mounted_ext4_disk, sr_disk_wiped

### pytest hooks

def pytest_addoption(parser):
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
        "--sr-disk",
        action="append",
        default=[],
        help="Name of an available disk (sdb) or partition device (sdb2) to be formatted and used in storage tests. "
             "Set it to 'auto' to let the fixtures auto-detect available disks."
    )
    parser.addoption(
        "--sr-disk-4k",
        action="append",
        default=[],
        help="Name of an available disk (sdb) or partition device (sdb2) with "
             "4KiB blocksize to be formatted and used in storage tests. "
             "Set it to 'auto' to let the fixtures auto-detect available disks."
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

def pytest_collection_modifyitems(items, config):
    # Automatically mark tests based on fixtures they require.
    # Check pytest.ini or pytest --markers for marker descriptions.

    markable_fixtures = [
        'uefi_vm',
        'unix_vm',
        'windows_vm',
        'hostA2',
        'hostB1',
        'sr_disk',
        'sr_disk_4k'
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
# adapted from https://docs.pytest.org/en/latest/example/simple.html#making-test-result-information-available-in-fixtures

# FIXME we may have to move this into lib/ if fixtures in sub-packages
# want to make use of this feature
from pytest import StashKey, CollectReport
PHASE_REPORT_KEY = StashKey[dict[str, CollectReport]]()
PHASE_REPORT_CHILDREN_KEY = StashKey[dict[str, CollectReport]]()

@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    # execute all other hooks to obtain the report object
    rep = yield

    # store test results for each phase of a call, which can
    # be "setup", "call", "teardown"
    item.stash.setdefault(PHASE_REPORT_KEY, {})[rep.when] = rep

    # if this test is within a class, make it accessible from the class
    parent_class = item.getparent(_pytest.python.Class)
    if parent_class:
        parent_class.stash.setdefault(PHASE_REPORT_CHILDREN_KEY, []).append(item)

    return rep

# END make test results visible from fixtures


### fixtures

def setup_host(hostname_or_ip):
    pool = Pool(hostname_or_ip)
    h = pool.master
    return h

@pytest.fixture(scope='session')
def hosts(pytestconfig):
    # a list of master hosts, each from a different pool
    hosts_args = pytestconfig.getoption("hosts")
    hosts_split = [hostlist.split(',') for hostlist in hosts_args]
    hostname_list = list(itertools.chain(*hosts_split))
    host_list = [setup_host(hostname_or_ip) for hostname_or_ip in hostname_list]
    if not host_list:
        pytest.fail("This test requires at least one --hosts parameter")
    yield host_list

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

@pytest.fixture(scope='session')
def local_sr_on_hostA1(hostA1):
    """ A local SR on the pool's master. """
    srs = hostA1.local_vm_srs()
    assert len(srs) > 0, "a local SR is required on the pool's master"
    # use the first local SR found
    sr = srs[0]
    logging.info(">> local SR on hostA1 present : %s" % sr.uuid)
    yield sr

@pytest.fixture(scope='session')
def local_sr_on_hostA2(hostA2):
    """ A local SR on the pool's second host. """
    srs = hostA2.local_vm_srs()
    assert len(srs) > 0, "a local SR is required on the pool's second host"
    # use the first local SR found
    sr = srs[0]
    logging.info(">> local SR on hostA2 present : %s" % sr.uuid)
    yield sr

@pytest.fixture(scope='session')
def local_sr_on_hostB1(hostB1):
    """ A local SR on the second pool's master. """
    srs = hostB1.local_vm_srs()
    assert len(srs) > 0, "a local SR is required on the second pool's master"
    # use the first local SR found
    sr = srs[0]
    logging.info(">> local SR on hostB1 present : %s" % sr.uuid)
    yield sr

@pytest.fixture(scope='session')
def sr_disk(pytestconfig, host):
    disks = pytestconfig.getoption("sr_disk")
    if len(disks) != 1:
        pytest.fail("This test requires exactly one --sr-disk parameter")
    disk = disks[0]
    if disk == "auto":
        logging.info(">> Check for the presence of a free disk device on the master host")
        disks = host.available_disks()
        assert len(disks) > 0, "a free disk device is required on the master host"
        disk = disks[0]
        logging.info(f">> Found free disk device(s) on hostA1: {' '.join(disks)}. Using {disk}.")
    else:
        logging.info(f">> Check that disk or block device {disk} is available on the master host")
        assert disk in host.available_disks(), \
            f"disk or block device {disk} is either not present or already used on master host"
    yield disk

@pytest.fixture(scope='session')
def sr_disk_4k(pytestconfig, host):
    disks = pytestconfig.getoption("sr_disk_4k")
    if len(disks) != 1:
        pytest.fail("This test requires exactly one --sr-disks-4k parameter")
    disk = disks[0]
    if disk == "auto":
        logging.info(">> Check for the presence of a free 4KiB block device on the master host")
        disks = host.available_disks(4096)
        assert len(disks) > 0, "a free 4KiB block device is required on the master host"
        disk = disks[0]
        logging.info(f">> Found free 4KiB block device(s) on hostA1: {' '.join(disks)}. Using {disk}.")
    else:
        logging.info(f">> Check that 4KiB block device {disk} is available on the master host")
        assert disk in host.available_disks(4096), \
            f"4KiB block device {disk} must be available for use on master host"
    yield disk

@pytest.fixture(scope='session')
def sr_disk_for_all_hosts(pytestconfig, request, host):
    disks = pytestconfig.getoption("sr_disk")
    if len(disks) != 1:
        pytest.fail("This test requires exactly one --sr-disk parameter")
    disk = disks[0]
    master_disks = host.available_disks()
    assert len(master_disks) > 0, "a free disk device is required on the master host"

    if disk != "auto":
        assert disk in master_disks, \
            f"disk or block device {disk} is either not present or already used on master host"
        master_disks = [disk]

    candidates = list(master_disks)
    for h in host.pool.hosts[1:]:
        other_disks = h.available_disks()
        candidates = [d for d in candidates if d in other_disks]

    if disk == "auto":
        assert len(candidates) > 0, \
            f"a free disk device is required on all pool members. Pool master has: {' '.join(master_disks)}."
        logging.info(f">> Found free disk device(s) on all pool hosts: {' '.join(candidates)}. Using {candidates[0]}.")
    else:
        assert len(candidates) > 0, \
            f"disk or block device {disk} was not found to be present and free on all hosts"
        logging.info(f">> Disk or block device {disk} is present and free on all pool members")
    yield candidates[0]

@pytest.fixture(scope='module')
def vm_ref(request):
    ref = request.param

    if ref is None:
        # get default VM from test if there's one
        marker = request.node.get_closest_marker("default_vm")
        default_vm = marker.args[0] if marker is not None else None
        if default_vm is not None:
            logging.info(">> No VM specified on CLI. Using default: %s." % default_vm)
            ref = default_vm
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
    # Do we cache VMs?
    try:
        from data import CACHE_IMPORTED_VM
    except ImportError:
        CACHE_IMPORTED_VM = False
    assert CACHE_IMPORTED_VM in [True, False]

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

@pytest.fixture(scope="class")
def create_vms(request, host):
    """
    Returns list of VM objects created from `vm_definitions` marker.

    `vm_definitions` marker test author to specify one or more VMs,
    using one `dict` per VM.

    Mandatory keys:
    - `name`: name of the VM to create (str)
    - `template`: name (or UUID) of template to use (str)

    Optional keys:
    - `vdis`: dict-specifications for VDIs (Iterable[dict[str, str]])
      Mandatory keys:
      - `name`
      - `size`

    Example:
    -------
    > @pytest.mark.vm_definitions(dict(name="vm1", template="Other install media"),
    >                             dict(name="vm2",
    >                                  template="CentOS 7",
    >                                  vdis=[dict(name="vm 2 system disk",
    >                                             size="100GiB",
    >                                             device="xvda",
    >                                             userdevice="0",
    >                                             )],
    >
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
    # Do we cache VMs?
    try:
        from data import CACHE_IMPORTED_VM
    except ImportError:
        CACHE_IMPORTED_VM = False
    assert CACHE_IMPORTED_VM in [True, False]

    marker = request.node.get_closest_marker("vm_definitions")
    if marker is None:
        raise Exception("No vm_definitions marker specified.")
    param_mapping = marker.kwargs.get("param_mapping", {})

    vm_defs = []
    for vm_def in marker.args:
        vm_def = callable_marker(vm_def, request, param_mapping=param_mapping)
        assert "name" in vm_def
        assert "template" in vm_def or "image_test" in vm_def
        if "template" in vm_def:
            assert not "image_test" in vm_def
        # FIXME should check optional vdis contents
        # FIXME should check for extra args
        vm_defs.append(vm_def)

    try:
        vms = []
        vdis = []
        vbds = []
        for vm_def in vm_defs:
            if "template" in vm_def:
                _create_vm(vm_def, host, vms, vdis, vbds)
            elif "image_test" in vm_def:
                _import_vm(request, vm_def, host, vms, use_cache=CACHE_IMPORTED_VM)

        yield vms

        # this is a "class" fixture, but it can be applied to functions too
        if isinstance(request.node, _pytest.python.Function):
            all_tests = [request.node]
        elif isinstance(request.node, _pytest.python.Class):
            all_tests = request.node.stash[PHASE_REPORT_CHILDREN_KEY]
        else:
            assert False, f"unexpected call scope for a class-scoped fixture: {request.node}"

        # only export VMs to cache if all tests in the class succeed
        for test_node in all_tests:
            report = test_node.stash[PHASE_REPORT_KEY]
            if report["setup"].failed:
                logging.warning("setting up %s failed: not exporting VMs", test_node.nodeid)
                break
            elif report["setup"].skipped:
                logging.warning("setting up %s skipped: not exporting VMs", test_node.nodeid)
                break
            elif "call" not in report: # can this even happen?
                logging.warning("%s not run: not exporting VMs", test_node.nodeid)
                break
            elif report["call"].failed:
                logging.warning("%s failed: not exporting VMs", test_node.nodeid)
                break
            elif report["call"].skipped: # can this even happen?
                logging.warning("%s skipped: not exporting VMs", test_node.nodeid)
                break
        else:
            # record this state
            for vm_def, vm in zip(vm_defs, vms):
                # FIXME where to store?
                xva_name = f"{shortened_nodeid(request.node.nodeid)}-{vm_def['name']}.xva"
                host.ssh(["rm -f", xva_name])
                vm.export(xva_name, "zstd", use_cache=CACHE_IMPORTED_VM)

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

def _create_vm(vm_def, host, vms, vdis, vbds):
    vm_name = vm_def["name"]
    vm_template = vm_def["template"]

    logging.info(">> Install VM %r from template %r", vm_name, vm_template)

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

    if "vifs" in vm_def:
        for vif_def in vm_def["vifs"]:
            vm.create_vif(vif_def["index"], vif_def["network_uuid"])

    if "params" in vm_def:
        for param_def in vm_def["params"]:
            logging.info("Setting param %s", param_def)
            vm.param_set(**param_def)

def _import_vm(request, vm_def, host, vms, *, use_cache):
    vm_image = xva_name_from_def(vm_def, request.node.nodeid)
    base_vm = host.import_vm(vm_image, sr_uuid=host.main_sr_uuid(), use_cache=use_cache)

    if use_cache:
        # Clone the VM before running tests, so that the original VM remains untouched
        logging.info(">> Clone cached VM before running tests")
        vm = base_vm.clone()
        # Remove the description, which may contain a cache identifier
        vm.param_set('name-description', "")
    else:
        vm = base_vm
    vms.append(vm)

@pytest.fixture(scope="module")
def running_vm(imported_vm):
    vm = imported_vm

    # may be already running if we skipped the import to use an existing VM
    if not vm.is_running():
        vm.start()
    wait_for(vm.is_running, '> Wait for VM running')
    wait_for(vm.try_get_and_store_ip, "> Wait for VM IP", timeout_secs=5*60)
    wait_for(vm.is_ssh_up, "> Wait for VM SSH up")
    return vm
    # no teardown

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
