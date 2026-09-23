import pytest

import os

from lib.host import Host
from lib.sr import SR

from .conftest import DIRECTORIES_PATH

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host >= 8.2

class TestFSPVDI:
    def test_create_and_destroy_VDI(self, host_with_fsp: Host, fsp_sr: SR, fsp_config: dict[str, str]) -> None:
        linkname = 'vdifor' + os.path.basename(fsp_config['shared_dir_path'])
        source = fsp_config['shared_dir_path']
        destination = fsp_config['db_path'] + '/' + DIRECTORIES_PATH + '/' + linkname
        host_with_fsp.ssh(f'ln -s {source} {destination}')
        # scan SR to detect the new VDI
        fsp_sr.scan()
        vdi_uuids = fsp_sr.vdi_uuids(managed=True, name_label=linkname)
        # fail if the vdi could not be created or it already exists
        assert len(vdi_uuids) == 1
        host_with_fsp.ssh(f'rm -f {fsp_config["db_path"]}/{DIRECTORIES_PATH}/{linkname}')
        fsp_sr.scan()
        vdi_uuids = fsp_sr.vdi_uuids(managed=True, name_label=linkname)
        # fail if the vdi could not be destroyed
        assert len(vdi_uuids) == 0
