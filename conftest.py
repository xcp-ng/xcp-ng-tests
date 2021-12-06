import logging
import pytest
import tempfile
# import json
import lib.config as global_config
from lib.common import wait_for, VM, Host, vm_image, is_uuid

# *** Support for incremental tests in test classes ***
# From https://stackoverflow.com/questions/12411431/how-to-skip-the-rest-of-tests-in-the-class-if-one-has-failed
def pytest_runtest_makereport(item, call):
    if "incremental" in item.keywords:
        if call.excinfo is not None:
            parent = item.parent
            parent._previousfailed = item

def pytest_runtest_setup(item):
    previousfailed = getattr(item.parent, "_previousfailed", None)
    if previousfailed is not None:
        pytest.skip("previous test failed (%s)" % previousfailed.name)

# *** End of: Support for incremental tests ***

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
        "--vms",
        action="append",
        default=[],
        help="VM keys or OVA URLs for tests that require several VMs",
    )
    parser.addoption(
        "--sr-device-config",
        action="append",
        default=[],
        help="device-config keys and values for a remote SR. "
             "Example: 'server:10.0.0.1,serverpath:/vms,nfsversion:4.1'.",
    )
    parser.addoption(
        "--additional-repos",
        action="append",
        default=[],
        help="Additional repo URLs added to the yum config"
    )
    parser.addoption(
        "--second-network",
        action="append",
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

def pytest_configure(config):
    global_config.ignore_ssh_banner = config.getoption('--ignore-ssh-banner')
    global_config.ssh_output_max_lines = int(config.getoption('--ssh-output-max-lines'))

def host_data(hostname_or_ip):
    # read from data.py
    from data import HOST_DEFAULT_USER, HOST_DEFAULT_PASSWORD, HOSTS
    if hostname_or_ip in HOSTS:
        h_data = HOSTS[hostname_or_ip]
        return h_data
    else:
        return {'user': HOST_DEFAULT_USER, 'password': HOST_DEFAULT_PASSWORD}

def setup_host(hostname_or_ip):
    logging.info(">>> Connect host %s" % hostname_or_ip)
    h = Host(hostname_or_ip)
    h.initialize()
    assert h.is_master(), "we connect only to master hosts during initial setup"
    # XO connection
    h_data = host_data(hostname_or_ip)
    skip_xo_config = h_data.get('skip_xo_config', False)
    if not skip_xo_config:
        h.xo_server_add(h_data['user'], h_data['password'])
    else:
        h.xo_get_server_id(store=True)
    wait_for(h.xo_server_connected, timeout_secs=10)
    return h, skip_xo_config

@pytest.fixture(scope='session')
def hosts(request):
    # a list of master hosts, each from a different pool
    hostname_list = request.param.split(',')
    host_list = [setup_host(hostname_or_ip) for hostname_or_ip in hostname_list]
    yield [tup[0] for tup in host_list]
    # teardown
    for h, skip_xo_config in host_list:
        if not skip_xo_config:
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
def sr_disk(host):
    disks = host.disks()
    # there must be at least 2 disks
    assert len(disks) > 1, "at least two disks are required on the first host"
    # Using the second disk for SR
    disk = disks[1]
    logging.info(">> a second disk for a local SR is present on hostA1: %s" % disk)
    yield disk

@pytest.fixture(scope='session')
def sr_disk_wiped(host, sr_disk):
    logging.info(">> wipe disk %s" % sr_disk)
    host.ssh(['wipefs', '-a', '/dev/' + sr_disk])
    yield sr_disk

@pytest.fixture(scope='session')
def sr_disk_for_all_hosts(host, sr_disk):
    for h in host.pool.hosts[1:]:
        disks = h.disks()
        # there must be at least 2 disks
        assert len(disks) > 1, "at least two disks are required on all pool's hosts, missing on host: %s" % h
        # Using the second disk for SR
        disk = next(d for d in disks if d == sr_disk)
        logging.info(">> a second disk for a local SR is present on host %s: %s" % (h, disk))
    yield sr_disk

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

@pytest.fixture
def vm_refs(request):
    vm_list = request.param

    if vm_list is None:
        # get default list of VMs from test if there's one
        marker = request.node.get_closest_marker("default_vms")
        default_vms = marker.args[0] if marker is not None else None
        if default_vms is not None:
            logging.info(">> No VM list specified on CLI. Using default: %s." % " ".join(default_vms))
            vm_list = default_vms
        else:
            # global default
            logging.info(
                ">> No VM list specified on CLI, and no default found in test definition. Using global default."
            )
            vm_list = ['mini-linux-x86_64-bios', 'mini-linux-x86_64-uefi']
    # TODO: finish implementation using vm_image

# TODO: make it a fixture factory?
@pytest.fixture(scope="module")
def imported_vm(host, vm_ref):
    if is_uuid(vm_ref):
        vm = VM(vm_ref, host)
        name = vm.name()
        logging.info(">> Reuse VM %s (%s) on host %s" % (vm_ref, name, host))
    else:
        vm = host.import_vm(vm_ref)
    yield vm
    # teardown
    if not is_uuid(vm_ref):
        logging.info("<< Destroy VM")
        vm.destroy(verify=True)

# TODO: make it a fixture factory?
@pytest.fixture(scope="module")
def running_vm(imported_vm):
    vm = imported_vm

    # may be already running if we skipped the import to use an existing VM
    if not vm.is_running():
        vm.start()
    wait_for(vm.is_running, '> Wait for VM running')
    wait_for(vm.try_get_and_store_ip, "> Wait for VM IP")
    wait_for(vm.is_ssh_up, "> Wait for VM SSH up")
    return vm
    # no teardown

@pytest.fixture(scope='session')
def sr_device_config(request):
    raw_config = request.param

    if raw_config is None:
        # Use defaults
        return None

    config = {}
    for key_val in raw_config.split(','):
        key = key_val.split(':')[0]
        value = key_val[key_val.index(':') + 1:]
        config[key] = value
    return config

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
def second_network(request):
    return request.param

def pytest_generate_tests(metafunc):
    if "hosts" in metafunc.fixturenames:
        metafunc.parametrize("hosts", metafunc.config.getoption("hosts"), indirect=True, scope="session")
    if "vm_ref" in metafunc.fixturenames:
        vms = metafunc.config.getoption("vm")
        if not vms:
            vms = [None] # no --vm parameter does not mean skip the test, for us, it means use the default
        metafunc.parametrize("vm_ref", vms, indirect=True, scope="module")
    if "vm_refs" in metafunc.fixturenames:
        vm_lists = metafunc.config.getoption("vms")
        if not vm_lists:
            vm_lists = [None] # no --vms parameter does not mean skip the test, for us, it means use the default
        metafunc.parametrize("vm_refs", vm_lists, indirect=True, scope="module")
    if "sr_device_config" in metafunc.fixturenames:
        configs = metafunc.config.getoption("sr_device_config")
        if not configs:
            # No --sr-device-config parameter doesn't mean skip the test.
            # For us it means use the defaults.
            configs = [None]
        metafunc.parametrize("sr_device_config", configs, indirect=True, scope="session")
    if "additional_repos" in metafunc.fixturenames:
        repos = metafunc.config.getoption("additional_repos")
        if not repos:
            # No --additional-repos parameter doesn't mean skip the test.
            # It's an optional parameter, if missing we must execute additional_repos fixture
            # without error.
            repos = [None]
        metafunc.parametrize("additional_repos", repos, indirect=True, scope="session")
    if "second_network" in metafunc.fixturenames:
        second_network = metafunc.config.getoption("second_network")
        if second_network is not None:
            metafunc.parametrize("second_network", second_network, indirect=True, scope="session")
