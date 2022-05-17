import logging
import pytest

from lib.pool import Pool

def enable_moosefs(host):
    assert not host.file_exists('/usr/sbin/mount.moosefs'), \
        "MooseFS client should not be installed on the host before all tests"
    host.ssh(['curl https://ppa.moosefs.com/RPM-GPG-KEY-MooseFS > /etc/pki/rpm-gpg/RPM-GPG-KEY-MooseFS'])
    host.ssh(['curl http://ppa.moosefs.com/MooseFS-3-el7.repo > /etc/yum.repos.d/MooseFS.repo'])
    host.yum_save_state()
    host.yum_install(['fuse', 'moosefs-client'])

    host.activate_smapi_driver('moosefs')

def disable_moosefs(host):
    host.deactivate_smapi_driver('moosefs')

    host.yum_restore_saved_state()
    host.ssh(['rm -f /etc/pki/rpm-gpg/RPM-GPG-KEY-MooseFS'])
    host.ssh(['rm -f /etc/yum.repos.d/MooseFS.repo'])

@pytest.fixture(scope='package')
def pool_with_moosefs(hostA1):
    pool = Pool(hostA1.hostname_or_ip)
    for host in pool.hosts:
        enable_moosefs(host)

    yield pool

    for host in pool.hosts:
        disable_moosefs(host)

@pytest.fixture(scope='package')
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

@pytest.fixture(scope='package')
def moosefs_sr(moosefs_device_config, pool_with_moosefs):
    """ MooseFS SR on a specific host. """
    sr = pool_with_moosefs.master.sr_create('moosefs', "MooseFS-SR-test", moosefs_device_config, shared=True)
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vm_on_moosefs_sr(host, moosefs_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=moosefs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
