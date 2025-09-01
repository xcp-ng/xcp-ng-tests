import pytest

import logging

from .utils import compare_data, copy_data_to_tapdev, operation_on_vdi, wait_for_vdi_coalesce

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vdi import VDI

class Test:
    def test_write_data(self, host_with_xxd: "Host", tapdev: str, data_file_on_host: str):
        length = 1 * 1024 * 1024
        offset = 0

        logging.info("Copying data to tapdev")
        copy_data_to_tapdev(host_with_xxd, data_file_on_host, tapdev, offset, length)

        logging.info("Comparing data to tapdev")
        assert compare_data(host_with_xxd, tapdev, data_file_on_host, offset, length)

    @pytest.mark.parametrize("vdi_op", ["snapshot", "clone"])
    def test_coalesce(
            self,
            host_with_xxd: "Host",
            tapdev: str,
            vdi_with_vbd_on_dom0: "VDI",
            data_file_on_host: str,
            vdi_op
    ):
        vdi = vdi_with_vbd_on_dom0
        vdi_uuid = vdi.uuid
        length = 1 * 1024 * 1024
        offset = 0

        new_vdi = operation_on_vdi(host_with_xxd, vdi_uuid, vdi_op)

        logging.info("Copying data to tapdev")
        copy_data_to_tapdev(host_with_xxd, data_file_on_host, tapdev, offset, length)

        logging.info(f"Removing VDI {vdi_op}")
        host_with_xxd.xe("vdi-destroy", {"uuid": new_vdi})

        wait_for_vdi_coalesce(vdi)

        logging.info("Comparing data to tapdev")
        assert compare_data(host_with_xxd, tapdev, data_file_on_host, offset, length)
