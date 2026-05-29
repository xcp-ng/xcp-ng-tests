from __future__ import annotations

import pytest

import logging

from lib.host import Host

from ..conftest import create_local_iso_sr

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host, with:
#   - the default SR being either a shared SR, or a local SR on the master host
#   - an additional unused disk for the SR
# From --vm parameter:
# - A VM to import

class TestLocalISOSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction
    """

    def test_create_sr_with_bad_location(self, host: Host) -> None:
        sr = None
        try:
            device_config = {
                'location': '/this/does/not/exist',
                'legacy_mode': 'true'
            }
            sr = host.sr_create('iso', "ISO-local-SR-test", device_config, verify=True)
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"

    def test_create_and_destroy_sr(self, host: Host, formatted_and_mounted_ext4_disk: str) -> None:
        location = formatted_and_mounted_ext4_disk + '/iso_sr'
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = create_local_iso_sr(host, location)
        sr.destroy(verify=True)
