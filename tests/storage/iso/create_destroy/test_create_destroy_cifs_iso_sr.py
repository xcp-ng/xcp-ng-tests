from __future__ import annotations

import pytest

import logging

from lib.host import Host

from ..conftest import check_iso_mount_and_read_from_vm, copy_tools_iso_to_iso_sr, remove_iso_from_sr

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

    def test_create_sr_with_bad_location(self, host: Host, cifs_iso_device_config: dict[str, str]) -> None:
        sr = None
        try:
            wrong_device_config = cifs_iso_device_config.copy()
            wrong_device_config['location'] += r'\wrongpath'
            sr = host.sr_create('iso', "ISO-CIFS-SR-test", wrong_device_config, verify=True)
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.forget()
            assert False, "SR creation should not have succeeded!"

    def test_create_and_destroy_sr(self, host: Host, cifs_iso_device_config: dict[str, str]) -> None:
        sr = host.sr_create('iso', "ISO-CIFS-SR-test", cifs_iso_device_config, shared=True, verify=True)
        sr.forget()
