import pytest
#import json
from lib.common import wait_for, wait_for_not, VM, Host, vm_image
from uuid import UUID

def pytest_addoption(parser):
    parser.addoption(
        "--host",
        action="append",
        default=[],
        help="XCP-ng or XS host for tests that require only one host",
    )
    parser.addoption(
        "--hosts",
        action="append",
        default=[],
        help="XCP-ng or XS list of hosts (comma-separated) for tests that require only one host",
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

def host_data(hostname_or_ip):
    # read from data.py
    from data import HOST_DEFAULT_USER, HOST_DEFAULT_PASSWORD, HOSTS
    if hostname_or_ip in HOSTS:
        h_data = HOSTS[hostname_or_ip]
        return h_data
    else:
        return {'user': HOST_DEFAULT_USER, 'password': HOST_DEFAULT_PASSWORD}

def setup_host(hostname_or_ip):
    print(">>> Connect host %s" % hostname_or_ip)
    h = Host(hostname_or_ip)
    h_data = host_data(hostname_or_ip)
    skip_xo_config = h_data.get('skip_xo_config', False)
    if not skip_xo_config:
        h.xo_server_add(h_data['user'], h_data['password'])
    h.initialize()
    wait_for(h.xo_server_connected, timeout_secs=10)
    return h, skip_xo_config

@pytest.fixture(scope='session')
def host(request):
    hostname_or_ip = request.param
    h, skip_xo_config = setup_host(hostname_or_ip)
    yield h
    # teardown
    if not skip_xo_config:
        print("<<< Disconnect host %s" % h)
        h.xo_server_remove()

@pytest.fixture
def hosts(request):
    # a list of hosts rather than a single host
    hostname_list = request.param.split(',')
    host_list = [setup_host(hostname_or_ip) for hostname_or_ip in hostname_list]
    yield [tup[0] for tup in host_list]
    # teardown
    for h, skip_xo_config in host_list:
        if not skip_xo_config:
            print("<<< Disconnect host %s" % h)
            h.xo_server_remove()

def is_uuid(maybe_uuid):
    try:
        UUID(maybe_uuid, version=4)
        return True
    except ValueError:
        return False

@pytest.fixture(scope='module')
def vm_ref(request):
    ref = request.param

    if ref is None:
        # get default VM from test if there's one
        marker = request.node.get_closest_marker("default_vm")
        default_vm = marker.args[0] if marker is not None else None
        if default_vm is not None:
            print(">> No VM specified on CLI. Using default: %s." % default_vm)
            ref = default_vm
        else:
            # global default
            print(">> No VM specified on CLI, and no default found in test definition. Using global default.")
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
            print(">> No VM list specified on CLI. Using default: %s." % " ".join(default_vms))
            vm_list = default_vms
        else:
            # global default
            print(">> No VM list specified on CLI, and no default found in test definition. Using global default.")
            vm_list = ['mini-linux-x86_64-bios', 'mini-linux-x86_64-uefi']
    # TODO: finish implementation using vm_image

# TODO: make it a fixture factory
@pytest.fixture(scope="module")
def imported_vm(host, vm_ref):
    if is_uuid(vm_ref):
        print(">> Reuse VM %s on host %s" % (vm_ref, host))
        vm = VM(vm_ref, host)
    else:
        print(">> ", end='')
        vm = host.import_vm_url(vm_ref)
    yield vm
    # teardown
    if not is_uuid(vm_ref):
        print("<< Destroy VM")
        vm.destroy()
        wait_for_not(vm.exists, "<< Wait for VM destroyed")

# TODO: make it a fixture factory?
@pytest.fixture(scope="module")
def running_linux_vm(imported_vm):
    vm = imported_vm

    # may be already running if we skipped the import to use an existing VM
    if not vm.is_running():
        print("> ", end='')
        vm.start()
    wait_for(vm.is_running, '> Wait for VM running')
    wait_for(vm.try_get_and_store_ip, "> Wait for VM IP")
    wait_for(vm.is_ssh_up, "> Wait for VM SSH up")
    return vm
    # no teardown

# @pytest.fixture(scope="session")
# def context():
#     # Sort of global context to pass general information and configuration to test functions
#     data = {}
#     with open('vms.json') as f:
#         data['VMs'] = json.loads(f.read())
#     return data

def pytest_generate_tests(metafunc):
    if "host" in metafunc.fixturenames:
        metafunc.parametrize("host", metafunc.config.getoption("host"), indirect=True, scope="session")
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
