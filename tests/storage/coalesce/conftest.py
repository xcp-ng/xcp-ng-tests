import pytest
import logging

from lib.vdi import VDI

MAX_LENGTH = 1 * 1024 * 1024 * 1024 # 1GiB

@pytest.fixture(scope="module")
def vdi_on_local_sr(host, local_sr_on_hostA1, image_format):
    sr_uuid = local_sr_on_hostA1.uuid
    vdi_uuid = host.xe("vdi-create",
                       {"sr-uuid": sr_uuid,
                        "name-label": "testVDI",
                        "virtual-size": str(MAX_LENGTH),
                        "sm-config:type": image_format,
                        })
    logging.info(">> Created VDI {} of type {}".format(vdi_uuid, image_format))

    vdi = VDI(vdi_uuid, host=host)

    yield vdi

    logging.info("<< Destroying VDI {}".format(vdi_uuid))
    host.xe("vdi-destroy", {"uuid": vdi_uuid})

@pytest.fixture(scope="module")
def vdi_with_vbd_on_dom0(host, vdi_on_local_sr):
    vdi_uuid = vdi_on_local_sr.uuid
    dom0_uuid = host.get_dom0_uuid()
    logging.info(f">> Plugging VDI {vdi_uuid} on Dom0")
    vbd_uuid = host.xe("vbd-create",
                       {"vdi-uuid": vdi_uuid,
                        "vm-uuid": dom0_uuid,
                        "device": "autodetect",
                        })
    host.xe("vbd-plug", {"uuid": vbd_uuid})

    yield vdi_on_local_sr

    logging.info(f"<< Unplugging VDI {vdi_uuid} from Dom0")
    host.xe("vbd-unplug", {"uuid": vbd_uuid})
    host.xe("vbd-destroy", {"uuid": vbd_uuid})

@pytest.fixture(scope="class")
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
    sr_uuid = local_sr_on_hostA1.uuid
    vdi_uuid = vdi_with_vbd_on_dom0.uuid
    yield f"/dev/sm/backend/{sr_uuid}/{vdi_uuid}"
