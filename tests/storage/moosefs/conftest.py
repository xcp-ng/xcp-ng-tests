import pytest

@pytest.fixture(scope='session')
def host_with_moosefs(host):
    assert not host.file_exists('/usr/sbin/mount.moosefs'), \
        "MooseFS client should not be installed on the host before all tests"
    host.ssh(['sh', '-c', '"curl https://ppa.moosefs.com/RPM-GPG-KEY-MooseFS > /etc/pki/rpm-gpg/RPM-GPG-KEY-MooseFS"'])
    host.ssh(['sh', '-c', '"curl http://ppa.moosefs.com/MooseFS-3-el7.repo > /etc/yum.repos.d/MooseFS.repo"'])
    host.yum_install(['fuse'], save_state=True)
    host.yum_install(['moosefs-client'])
    yield host
    # teardown
    host.yum_restore_saved_state()
    host.ssh(['sh', '-c', '"rm -f /etc/pki/rpm-gpg/RPM-GPG-KEY-MooseFS"'])
    host.ssh(['sh', '-c', '"rm -f /etc/yum.repos.d/MooseFS.repo"'])

@pytest.fixture(scope='session')
def moosefs_device_config(sr_device_config):
    if sr_device_config is not None:
        # SR device config from CLI param
        config = sr_device_config
    else:
        # SR device config from data.py defaults
        try:
            from data import DEFAULT_MOOSEFS_DEVICE_CONFIG
        except ImportError:
            DEFAULT_MOOSEFS_DEVICE_CONFIG = {}
        if DEFAULT_MOOSEFS_DEVICE_CONFIG:
            config = DEFAULT_MOOSEFS_DEVICE_CONFIG
        else:
            raise Exception("No default MooseFS device-config found, neither in CLI nor in data.py defaults")
    return config

@pytest.fixture(scope='session')
def moosefs_sr(moosefs_device_config, host_with_moosefs):
    """ MooseFS SR on a specyfic host """
    sr = host_with_moosefs.sr_create('moosefs', "MooseFS-SR-test", moosefs_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vm_on_moosefs_sr(host, moosefs_sr, vm_ref):
    print(">> ", end='')
    vm = host.import_vm(vm_ref, sr_uuid=moosefs_sr.uuid)
    yield vm
    # teardown
    print("<< Destroy VM")
    vm.destroy(verify=True)

@pytest.fixture(scope='module')
def pass_vm_ref(vm_ref):
    return vm_ref
