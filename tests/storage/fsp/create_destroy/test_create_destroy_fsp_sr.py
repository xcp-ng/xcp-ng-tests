from __future__ import annotations

import pytest

import logging

from lib.host import Host

from ..conftest import DIRECTORIES_PATH

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host >= 8.2

class TestFSPSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR
    because they precisely need to test SR creation and destruction.
    """

    def test_create_and_destroy_sr(self, host_with_fsp: Host) -> None:
        db_path = host_with_fsp.ssh('mktemp -d')
        host_with_fsp.ssh(f'mkdir {db_path}/{DIRECTORIES_PATH}')
        sr = host_with_fsp.sr_create('fsp', "fsp-local-SR-test", {'file-uri': db_path})
        sr.destroy()
        host_with_fsp.ssh(f'rm -rf {db_path}')

    def test_create_and_destroy_sr_non_existing_path(self, host_with_fsp: Host) -> None:
        # get an unique non-existing path
        db_path = host_with_fsp.ssh('mktemp -d --dry-run')
        sr = None
        try:
            sr = host_with_fsp.sr_create('fsp', "fsp-local-SR-test", {'file-uri': db_path})
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"
