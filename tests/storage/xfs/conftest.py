import logging
import pytest

@pytest.fixture(scope='package')
def host_with_xfsprogs(host):
    assert not host.file_exists('/usr/sbin/mkfs.xfs'), \
        "xfsprogs must not be installed on the host at the beginning of the tests"
    host.yum_save_state()
    host.yum_install(['xfsprogs'])
    yield host
    # teardown
    host.yum_restore_saved_state()

@pytest.fixture(scope='package')
def xfs_sr(sr_disk, host_with_xfsprogs):
    """ A XFS SR on first host. """
    sr = host_with_xfsprogs.sr_create('xfs', "XFS-local-SR-test", {'device': '/dev/' + sr_disk})
    yield sr
    # teardown
    sr.destroy()

@pytest.fixture(scope='module')
def vdi_on_xfs_sr(xfs_sr):
    vdi = xfs_sr.create_vdi('XFS-local-VDI-test')
    yield vdi
    vdi.destroy()

@pytest.fixture(scope='module')
def vm_on_xfs_sr(host, xfs_sr, vm_ref):
    vm = host.import_vm(vm_ref, sr_uuid=xfs_sr.uuid)
    yield vm
    # teardown
    logging.info("<< Destroy VM")
    vm.destroy(verify=True)
