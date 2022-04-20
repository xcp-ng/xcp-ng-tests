import pytest
import logging
import os
from .conftest import copy_tools_iso_to_iso_sr, check_iso_mount_and_read_from_vm, remove_iso_from_sr

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host
# From --vm parameter
# - A VM to import
# From data.py or --sr-device-config parameter: configuration to create a new CIFS (SAMBA) ISO SR.

class TestCIFSISOSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction
    """

    def test_create_sr_with_bad_location(self, host, cifs_iso_device_config):
        sr = None
        try:
            wrong_device_config = cifs_iso_device_config.copy()
            wrong_device_config['location'] += r'\wrongpath'
            host.sr_create('iso', "ISO-CIFS-SR-test", wrong_device_config, verify=True)
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.forget()
            assert False, "SR creation should not have succeeded!"

    def test_create_and_destroy_sr(self, host, cifs_iso_device_config):
        sr = host.sr_create('iso', "ISO-CIFS-SR-test", cifs_iso_device_config, shared=True, verify=True)
        sr.forget()

@pytest.mark.small_vm # run with a small VM to test the features
@pytest.mark.usefixtures("cifs_iso_sr")
class TestCIFSISOSR:

    def test_iso_mount_and_read(self, host, cifs_iso_sr, running_unix_vm):
        # create the ISO SR on CIFS
        sr = cifs_iso_sr
        iso_path = copy_tools_iso_to_iso_sr(host, sr)
        try:
            check_iso_mount_and_read_from_vm(host, os.path.basename(iso_path), running_unix_vm)
        finally:
            # SR cleaning
            remove_iso_from_sr(host, sr, iso_path)
