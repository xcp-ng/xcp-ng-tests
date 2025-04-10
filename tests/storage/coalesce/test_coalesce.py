import logging
import time

from lib.host import Host

def copy_data_to_tapdev(host: Host, data_file: str, tapdev: str, offset: int, length: int):
    """
    if offset == 0:
        off = "0"
    else:
        off = f"{offset}B" # Doesn't work with `dd` version of XCP-ng 8.3
    """
    bs = 1
    off = int(offset / bs)
    count = length / bs
    count += length % bs
    count = int(count)
    cmd = ["dd", f"if={data_file}", f"of={tapdev}", f"bs={bs}", f"seek={off}", f"count={count}"]
    host.ssh(cmd)

def get_data(host: Host, file: str, offset: int, length: int, checksum: bool = False) -> str:
    cmd = ["xxd", "-p", "-seek", str(offset), "-len", str(length), file]
    if checksum:
        cmd = cmd + ["|", "sha256sum"]
    return host.ssh(cmd)

def get_hashed_data(host: Host, file: str, offset: int, length: int):
    return get_data(host, file, offset, length, True).split()[0]

def snapshot_vdi(host: Host, vdi_uuid: str):
    vdi_snap = host.xe("vdi-snapshot", {"uuid": vdi_uuid})
    logging.info(f"Snapshot VDI {vdi_uuid}: {vdi_snap}")
    return vdi_snap

def compare_data(host: Host, tapdev: str, data_file: str, offset: int, length: int) -> bool:
    logging.info("Getting data from VDI and file")
    vdi_checksum = get_hashed_data(host, tapdev, offset, length)
    file_checksum = get_hashed_data(host, data_file, 0, length)
    logging.info(f"VDI: {vdi_checksum}")
    logging.info(f"FILE: {file_checksum}")

    return vdi_checksum == file_checksum

def test_write_data(host, tapdev, data_file_on_host):
    length = 1 * 1024 * 1024
    offset = 0

    logging.info("Copying data to tapdev")
    copy_data_to_tapdev(host, data_file_on_host, tapdev, offset, length)

    assert compare_data(host, tapdev, data_file_on_host, offset, length)

def test_coalesce(host, tapdev, vdi_with_vbd_on_dom0, data_file_on_host):
    vdi = vdi_with_vbd_on_dom0
    vdi_uuid = vdi.uuid
    length = 1 * 1024 * 1024
    offset = 0

    vdi_snap = snapshot_vdi(host, vdi_uuid)

    logging.info("Copying data to tapdev")
    copy_data_to_tapdev(host, data_file_on_host, tapdev, offset, length)

    logging.info("Removing VDI snapshot")
    host.xe("vdi-destroy", {"uuid": vdi_snap})

    logging.info("Waiting for coalesce")
    while vdi.get_parent() is not None:
        time.sleep(1)
    logging.info("Coalesce done")

    assert compare_data(host, tapdev, data_file_on_host, offset, length)

def test_clone_coalesce(host, tapdev, vdi_with_vbd_on_dom0, data_file_on_host):
    vdi = vdi_with_vbd_on_dom0
    vdi_uuid = vdi.uuid
    length = 1 * 1024 * 1024
    offset = 0

    clone_uuid = host.xe("vdi-clone", {"uuid": vdi_uuid})
    logging.info(f"Clone VDI {vdi_uuid}: {clone_uuid}")

    logging.info("Copying data to tapdev")
    copy_data_to_tapdev(host, data_file_on_host, tapdev, offset, length)

    host.xe("vdi-destroy", {"uuid": clone_uuid})

    logging.info("Waiting for coalesce")
    while vdi.get_parent() is not None:
        time.sleep(1)
    logging.info("Coalesce done")

    assert compare_data(host, tapdev, data_file_on_host, offset, length)
