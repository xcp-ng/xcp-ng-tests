import pytest

import logging

from lib.host import Host

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host
# From data.py or --sr-device-config parameter: configuration to create a new NFS ISO SR.

class TestNFSISOSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction
    """

    def test_create_sr_with_bad_location(self, host: Host, nfs_iso_device_config: dict[str, str]) -> None:
        sr = None
        try:
            wrong_device_config = nfs_iso_device_config.copy()
            wrong_device_config['location'] += '/wrongpath'
            sr = host.sr_create('iso', "ISO-NFS-SR-test", wrong_device_config, verify=True)
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.forget()
            assert False, "SR creation should not have succeeded!"

    def test_create_and_destroy_sr(self, host: Host, nfs_iso_device_config: dict[str, str]) -> None:
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        sr = host.sr_create('iso', "ISO-NFS-SR-test", nfs_iso_device_config, shared=True, verify=True)
        sr.forget()
