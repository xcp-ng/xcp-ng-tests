import pytest

import logging

from lib.vdi import VDI

MAX_LENGTH = 1 * 1024 * 1024 * 1024 # 1GiB

@pytest.fixture(scope="module")
def vdi_on_local_sr(host, local_sr_on_hostA1, image_format):
    sr = local_sr_on_hostA1
    vdi = sr.create_vdi("testVDI", MAX_LENGTH, image_format=image_format)
    logging.info(">> Created VDI {} of type {}".format(vdi.uuid, image_format))

    yield vdi

    logging.info("<< Destroying VDI {}".format(vdi.uuid))
    vdi.destroy()

@pytest.fixture(scope="module")
def vdi_with_vbd_on_dom0(host, vdi_on_local_sr):
    dom0 = host.get_dom0_VM()
    dom0.connect_vdi(vdi_on_local_sr)

    yield vdi_on_local_sr

    dom0.disconnect_vdi(vdi_on_local_sr)

@pytest.fixture(scope="function")
def data_file_on_host(host):
    filename = "/root/data.bin"
    logging.info(f">> Creating data file {filename} on host")
    size = 1 * 1024 * 1024 # 1MiB
    assert size <= MAX_LENGTH, "Size of the data file bigger than the VDI size"

    host.ssh(["dd", "if=/dev/urandom", f"of={filename}", f"bs={size}", "count=1"])

    yield filename

    logging.info("<< Deleting data file")
    host.ssh(["rm", filename])

@pytest.fixture(scope="module")
def tapdev(local_sr_on_hostA1, vdi_with_vbd_on_dom0):
    """
    A tapdev is a blockdevice allowing access to a VDI from the Dom0.

    It is usually used to give access to the VDI to Qemu for emulating devices
    before PV driver are loaded in the guest.
    """
    sr_uuid = local_sr_on_hostA1.uuid
    vdi_uuid = vdi_with_vbd_on_dom0.uuid
    yield f"/dev/sm/backend/{sr_uuid}/{vdi_uuid}"
