import pytest

import logging
import os

from .conftest import DIRECTORIES_PATH

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host >= 8.2

class TestFSPSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR
    because they precisely need to test SR creation and destruction.
    """

    def test_create_and_destroy_sr(self, host_with_fsp):
        db_path = host_with_fsp.ssh(['mktemp', '-d'])
        host_with_fsp.ssh(['mkdir', db_path + '/' + DIRECTORIES_PATH])
        sr = host_with_fsp.sr_create('fsp', "fsp-local-SR-test", {'file-uri': db_path})
        sr.destroy()
        host_with_fsp.ssh(['rm', '-rf', db_path])

    def test_create_and_destroy_sr_non_existing_path(self, host_with_fsp):
        # get an unique non-existing path
        db_path = host_with_fsp.ssh(['mktemp', '-d', '--dry-run'])
        sr = None
        try:
            sr = host_with_fsp.sr_create('fsp', "fsp-local-SR-test", {'file-uri': db_path})
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"

class TestFSPVDI:
    def test_create_and_destroy_VDI(self, host_with_fsp, fsp_sr, fsp_config):
        linkname = 'vdifor' + os.path.basename(fsp_config['shared_dir_path'])
        source = fsp_config['shared_dir_path']
        destination = fsp_config['db_path'] + '/' + DIRECTORIES_PATH + '/' + linkname
        host_with_fsp.ssh(['ln', '-s', source, destination])
        # scan SR to detect the new VDI
        fsp_sr.scan()
        vdi_uuids = fsp_sr.vdi_uuids(managed=True, name_label=linkname)
        # fail if the vdi could not be created or it already exists
        assert len(vdi_uuids) == 1
        host_with_fsp.ssh(['rm', '-f', fsp_config['db_path'] + '/' + DIRECTORIES_PATH + '/' + linkname])
        fsp_sr.scan()
        vdi_uuids = fsp_sr.vdi_uuids(managed=True, name_label=linkname)
        # fail if the vdi could not be destroyed
        assert len(vdi_uuids) == 0
