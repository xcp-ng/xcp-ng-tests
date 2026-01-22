import pytest

import os
import time

# Explicitly import package-scoped fixtures (see explanation in pkgfixtures.py)
from lib.host import Host
from lib.vm import VM
from pkgfixtures import formatted_and_mounted_ext4_disk

def create_local_iso_sr(host: Host, location):
    host.ssh(f'mkdir -p {location}')
    device_config = {
        'location': location,
        'legacy_mode': 'true'
    }
    return host.sr_create('iso', "ISO-local-SR-test", device_config, verify=True)

@pytest.fixture(scope='module')
def local_iso_sr(host: Host, formatted_and_mounted_ext4_disk):
    """ An ISO SR on first host. """
    location = formatted_and_mounted_ext4_disk + '/iso_sr'
    sr = create_local_iso_sr(host, location)
    yield sr, location
    # teardown
    sr.destroy()

def copy_tools_iso_to_iso_sr(host: Host, sr, location=None):
    # copy the ISO file to the right location
    iso_path = host.ssh('find /opt/xensource/packages/iso/ -name "*.iso"')
    iso_new_name = sr.uuid + "_test.iso"
    if location is not None:
        iso_new_path = f"{location}/{iso_new_name}"
    else:
        iso_new_path = f"/run/sr-mount/{sr.uuid}/{iso_new_name}"
    host.ssh(f'cp -f {iso_path} {iso_new_path}')
    sr.scan()
    return iso_new_path

def check_iso_mount_and_read_from_vm(host: Host, iso_name, vm: VM):
    """ Helper test function shared by several tests. """
    host.xe('vm-cd-insert', {'cd-name': iso_name, 'uuid': vm.uuid})
    try:
        mountpoint = vm.ssh('mktemp -d')
        time.sleep(2) # wait a small amount of time just to ensure the device is available
        vm.ssh(f'mount -t iso9660 /dev/cdrom {mountpoint}')
        try:
            file_to_test = mountpoint + '/Linux/install.sh'
            assert vm.file_exists(file_to_test)
            try:
                vm.ssh(f'cp -f {file_to_test} /tmp/')
            finally:
                vm.ssh(f'rm -f /tmp/{os.path.basename(file_to_test)}')
        finally:
            vm.ssh(f'umount {mountpoint}')
    finally:
        host.xe('vm-cd-eject', {'uuid': vm.uuid})

def remove_iso_from_sr(host: Host, sr, iso_path):
    host.ssh(f'rm -f {iso_path}')
    sr.scan()
