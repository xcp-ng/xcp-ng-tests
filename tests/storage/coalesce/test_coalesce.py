import logging

from .utils import wait_for_vdi_coalesce, copy_data_to_tapdev, snapshot_vdi, compare_data

class Test:
    def test_write_data(self, host, tapdev, data_file_on_host):
        length = 1 * 1024 * 1024
        offset = 0

        logging.info("Copying data to tapdev")
        copy_data_to_tapdev(host, data_file_on_host, tapdev, offset, length)

        assert compare_data(host, tapdev, data_file_on_host, offset, length)

    def test_coalesce(self, host, tapdev, vdi_with_vbd_on_dom0, data_file_on_host):
        vdi = vdi_with_vbd_on_dom0
        vdi_uuid = vdi.uuid
        length = 1 * 1024 * 1024
        offset = 0

        vdi_snap = snapshot_vdi(host, vdi_uuid)

        logging.info("Copying data to tapdev")
        copy_data_to_tapdev(host, data_file_on_host, tapdev, offset, length)

        logging.info("Removing VDI snapshot")
        host.xe("vdi-destroy", {"uuid": vdi_snap})

        wait_for_vdi_coalesce(vdi)

        assert compare_data(host, tapdev, data_file_on_host, offset, length)

    def test_clone_coalesce(self, host, tapdev, vdi_with_vbd_on_dom0, data_file_on_host):
        vdi = vdi_with_vbd_on_dom0
        vdi_uuid = vdi.uuid
        length = 1 * 1024 * 1024
        offset = 0

        clone_uuid = host.xe("vdi-clone", {"uuid": vdi_uuid})
        logging.info(f"Clone VDI {vdi_uuid}: {clone_uuid}")

        logging.info("Copying data to tapdev")
        copy_data_to_tapdev(host, data_file_on_host, tapdev, offset, length)

        logging.info("Removing VDI clone")
        host.xe("vdi-destroy", {"uuid": clone_uuid})

        wait_for_vdi_coalesce(vdi)

        assert compare_data(host, tapdev, data_file_on_host, offset, length)
