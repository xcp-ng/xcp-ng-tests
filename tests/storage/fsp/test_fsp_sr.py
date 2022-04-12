import pytest
import os
import logging

from .conftest import DIRECTORIES_PATH

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host >= 8.3

@pytest.mark.usefixtures("host_at_least_8_3")
class TestFSPSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR
    because they precisely need to test SR creation and destruction.
    """

    def test_create_and_destroy_sr(self, host):
        db_path = host.ssh(['mktemp', '-d'])
        host.ssh(['mkdir', db_path + '/' + DIRECTORIES_PATH])
        sr = host.sr_create('fsp', "fsp-local-SR-test", {'file-uri': db_path})
        sr.destroy()
        host.ssh(['rm', '-rf', db_path])

    def test_create_and_destroy_sr_non_existing_path(self, host):
        # get an unique non-existing path
        db_path = host.ssh(['mktemp', '-d', '--dry-run'])
        sr = None
        try:
            sr = host.sr_create('fsp', "fsp-local-SR-test", {'file-uri': db_path})
        except Exception:
            logging.info("SR creation failed, as expected.")
        if sr is not None:
            sr.destroy()
            assert False, "SR creation should not have succeeded!"

@pytest.mark.usefixtures("host_at_least_8_3")
class TestFSPVDI:
    def test_create_and_destroy_VDI(self, host, fsp_sr, fsp_config):
        linkname = 'vdifor' + os.path.basename(fsp_config['shared_dir_path'])
        source = fsp_config['shared_dir_path']
        destination = fsp_config['db_path'] + '/' + DIRECTORIES_PATH + '/' + linkname
        host.ssh(['ln', '-s', source, destination])
        # scan SR to detect the new VDI
        fsp_sr.scan()
        vdi_uuids = fsp_sr.vdi_uuids(managed=True, name_label=linkname)
        # fail if the vdi could not be created or it already exists
        assert len(vdi_uuids) == 1
        host.ssh(['rm', '-f', fsp_config['db_path'] + '/' + DIRECTORIES_PATH + '/' + linkname])
        fsp_sr.scan()
        vdi_uuids = fsp_sr.vdi_uuids(managed=True, name_label=linkname)
        # fail if the vdi could not be destroyed
        assert len(vdi_uuids) == 0
