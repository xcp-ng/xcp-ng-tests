import pytest
import logging
import os
from conftest import copy_tools_iso_to_iso_sr, check_iso_mount_and_read_from_vm, remove_iso_from_sr, create_local_iso_sr

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host
# From --sr-disk parameter:
# - an additional unused disk for the SR
# From --vm parameter:
# - A VM to import

class TestLocalISOSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction
    """

    def test_create_sr_with_bad_location(self, host):
        sr = None
        try:
            device_config = {
                'location': '/this/does/not/exist',
                'legacy_mode': 'true'
            }
            host.sr_create('iso', "ISO-local-SR-test", device_config, verify=True)
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"

    def test_create_and_destroy_sr(self, host, formatted_and_mounted_ext4_disk):
        location = formatted_and_mounted_ext4_disk + '/iso_sr'
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = create_local_iso_sr(host, location)
        sr.destroy(verify=True)

@pytest.mark.usefixtures("local_iso_sr")
class TestLocalISOSR:

    def test_iso_mount_and_read(self, host, local_iso_sr, running_vm):
        sr, location = local_iso_sr
        iso_path = copy_tools_iso_to_iso_sr(host, sr, location)
        try:
            check_iso_mount_and_read_from_vm(host, os.path.basename(iso_path), running_vm)
        finally:
            # SR cleaning
            remove_iso_from_sr(host, sr, iso_path)
