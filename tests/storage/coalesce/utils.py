import logging

from lib.common import wait_for_not
from lib.host import Host
from lib.vdi import VDI

from typing import Literal

def wait_for_vdi_coalesce(vdi: VDI):
    wait_for_not(lambda: vdi.get_parent(), msg="Waiting for coalesce")
    logging.info("Coalesce done")

def copy_data_to_tapdev(host: Host, data_file: str, tapdev: str, offset: int, length: int):
    # if offset == 0:
    #     off = "0"
    # else:
    #     off = f"{offset}B" # Doesn't work with `dd` version of XCP-ng 8.3

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

def operation_on_vdi(host: Host, vdi_uuid: str, vdi_op: Literal["snapshot", "clone"]) -> str:
    new_vdi = host.xe(f"vdi-{vdi_op}", {"uuid": vdi_uuid})
    logging.info(f"{vdi_op.capitalize()} VDI {vdi_uuid}: {new_vdi}")
    return new_vdi

def compare_data(host: Host, tapdev: str, data_file: str, offset: int, length: int) -> bool:
    logging.info("Getting data from VDI and file")
    vdi_checksum = get_hashed_data(host, tapdev, offset, length)
    file_checksum = get_hashed_data(host, data_file, 0, length)
    logging.info(f"VDI: {vdi_checksum}")
    logging.info(f"FILE: {file_checksum}")

    return vdi_checksum == file_checksum
