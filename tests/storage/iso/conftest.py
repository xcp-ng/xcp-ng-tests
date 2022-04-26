import pytest
import time
import os

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from pkgfixtures import formatted_and_mounted_ext4_disk

def create_local_iso_sr(host, location):
    host.ssh(['mkdir', '-p', location])
    device_config = {
        'location': location,
        'legacy_mode': 'true'
    }
    return host.sr_create('iso', "ISO-local-SR-test", device_config, verify=True)

@pytest.fixture(scope='package')
def local_iso_sr(host, formatted_and_mounted_ext4_disk):
    """ An ISO SR on first host. """
    location = formatted_and_mounted_ext4_disk + '/iso_sr'
    sr = create_local_iso_sr(host, location)
    yield sr, location
    # teardown
    sr.destroy()

@pytest.fixture(scope='package')
def nfs_iso_device_config(sr_device_config):
    if sr_device_config is not None:
        # SR device config from CLI param
        config = sr_device_config
    else:
        # SR device config from data.py defaults
        try:
            from data import DEFAULT_NFS_ISO_DEVICE_CONFIG
        except ImportError:
            DEFAULT_NFS_ISO_DEVICE_CONFIG = {}
        if DEFAULT_NFS_ISO_DEVICE_CONFIG:
            config = DEFAULT_NFS_ISO_DEVICE_CONFIG
        else:
            raise Exception("No default NFS ISO device-config found, neither in CLI nor in data.py defaults")
    return config

@pytest.fixture(scope='package')
def cifs_iso_device_config(sr_device_config):
    if sr_device_config is not None:
        # SR device config from CLI param
        config = sr_device_config
    else:
        # SR device config from data.py defaults
        try:
            from data import DEFAULT_CIFS_ISO_DEVICE_CONFIG
        except ImportError:
            DEFAULT_CIFS_ISO_DEVICE_CONFIG = {}
        if DEFAULT_CIFS_ISO_DEVICE_CONFIG:
            config = DEFAULT_CIFS_ISO_DEVICE_CONFIG
        else:
            raise Exception("No default CIFS ISO device-config found, neither in CLI nor in data.py defaults")
    return config

@pytest.fixture(scope='package')
def nfs_iso_sr(host, nfs_iso_device_config):
    """ A NFS ISO SR. """
    sr = host.sr_create('iso', "ISO-NFS-SR-test", nfs_iso_device_config, shared=True, verify=True)
    yield sr
    # teardown
    sr.forget()

@pytest.fixture(scope='package')
def cifs_iso_sr(host, cifs_iso_device_config):
    """ A Samba/CIFS SR. """
    sr = host.sr_create('iso', "ISO-CIFS-SR-test", cifs_iso_device_config, shared=True, verify=True)
    yield sr
    # teardown
    sr.forget()

def copy_tools_iso_to_iso_sr(host, sr, location=None):
    # copy the ISO file to the right location
    iso_path = host.ssh(['find', '/opt/xensource/packages/iso/', '-name', '"*.iso"'])
    iso_new_name = sr.uuid + "_test.iso"
    if location is not None:
        iso_new_path = f"{location}/{iso_new_name}"
    else:
        iso_new_path = f"/run/sr-mount/{sr.uuid}/{iso_new_name}"
    host.ssh(['cp', '-f', iso_path, iso_new_path])
    sr.scan()
    return iso_new_path

def check_iso_mount_and_read_from_vm(host, iso_name, vm):
    """ Helper test function shared by several tests. """
    host.xe('vm-cd-insert', {'cd-name': iso_name, 'uuid': vm.uuid})
    try:
        mountpoint = vm.ssh(['mktemp', '-d'])
        time.sleep(2) # wait a small amount of time just to ensure the device is available
        vm.ssh(['mount', '-t', 'iso9660', '/dev/cdrom', mountpoint])
        try:
            file_to_test = mountpoint + '/Linux/install.sh'
            assert vm.file_exists(file_to_test)
            try:
                vm.ssh(['cp', '-f', file_to_test, '/tmp/'])
            finally:
                vm.ssh(['rm', '-f', f'/tmp/{os.path.basename(file_to_test)}'])
        finally:
            vm.ssh(['umount', mountpoint])
    finally:
        host.xe('vm-cd-eject', {'uuid': vm.uuid})

def remove_iso_from_sr(host, sr, iso_path):
    host.ssh(['rm', '-f', iso_path])
    sr.scan()
