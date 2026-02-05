#!/usr/bin/env python3

import argparse
import logging
import os
import pathlib
import re
import subprocess
import tempfile

from lib.commands import SSHCommandFailed
from lib.common import wait_for
from lib.pool import Pool
from lib.vbd import VBD
from lib.vm import VM

# Tool to compact Windows VMs and create test XVAs


def get_partitions(part_table: str):
    for line in part_table.splitlines():
        match = re.match(
            (
                r'^(?P<device>[^\s]+) : '
                r'start=\s*(?P<start>[\d]+), '
                r'size=\s+(?P<size>[\d]+), '
                r'type=(?P<type>[A-F0-9\-]+), '
                r'uuid=(?P<uuid>[A-F0-9\-]+)(, .*)?$'
            ),
            line,
        )
        if match is None:
            continue
        yield match.groupdict()


def get_device_path(vbd: VBD):
    device = vbd.param_get("device")
    assert device
    return "/dev/" + device


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, metavar="HOST", help="host IP or hostname")
    parser.add_argument("--vm", nargs="+", required=True, metavar="VM_UUID", help="UUIDs of VMs to compact")
    parser.add_argument("--ntfsclone", default="ntfsclone", metavar="PATH", help="path to ntfsclone")
    parser.add_argument("--delete", action="store_true", help="delete original VDI after compacting")
    args = parser.parse_args()

    logging.getLogger().setLevel(logging.INFO)

    with open('/sys/hypervisor/uuid') as uuid_file:
        my_uuid = uuid_file.readline().strip()
    logging.info(f"My UUID is {my_uuid}")
    if not my_uuid:
        raise RuntimeError("cannot detect my own hypervisor UUID")

    pool = Pool(args.host)
    host = pool.master
    myself = VM(my_uuid, host)
    assert myself.is_running()

    with tempfile.TemporaryDirectory() as mountpoint:
        for vm_uuid in args.vm:
            logging.info(f"Compacting VM {vm_uuid}")
            vm = VM(vm_uuid, host)
            assert not vm.is_running()
            assert len(vm.vdis) == 1

            try:
                vm.eject_cd()
            except SSHCommandFailed:
                pass

            orig_vdi = vm.vdis[0]
            logging.info(f"Connecting VDI {orig_vdi.name()}")
            vm.disconnect_vdi(orig_vdi)
            orig_vbd = myself.connect_vdi(orig_vdi)
            orig_device_path = get_device_path(orig_vbd)
            assert os.path.exists(orig_device_path)

            logging.info(f"Cloning VDI {orig_vdi.name()}")
            compact_vdi = orig_vdi.sr.create_vdi(f"{orig_vdi.name()}-compacted", orig_vdi.get_virtual_size())
            compact_vbd = myself.connect_vdi(compact_vdi)
            compact_device_path = get_device_path(compact_vbd)
            wait_for(lambda: os.path.exists(compact_device_path), timeout_secs=30)

            # we don't use sfdisk -j since sfdisk can't consume that for the partition table copying
            logging.info("Reading original partition table")
            part_table = subprocess.run(
                ["sfdisk", "-d", orig_device_path],
                capture_output=True,
                check=True,
                encoding="utf-8",
            ).stdout

            logging.info("Cloning partition table")
            subprocess.run(
                ["sfdisk", compact_device_path],
                input=part_table,
                check=True,
                encoding="utf-8",
            )

            logging.info("Cloning partitions")
            for partition in get_partitions(part_table):
                logging.info(partition)
                orig_part = partition["device"]
                compact_part = partition["device"].replace(orig_device_path, compact_device_path)
                assert compact_part != orig_part
                part_type = subprocess.run(
                    ["blkid", "-s", "TYPE", "-o", "value", orig_part],
                    capture_output=True,
                    check=True,
                    encoding="utf-8",
                ).stdout.strip()
                logging.info(f"Original partition {orig_part} is of type {part_type}")
                if part_type == "ntfs":
                    logging.info(f"Cloning NTFS {orig_part} -> {compact_part}")
                    subprocess.run(["mount", orig_part, mountpoint], check=True)
                    try:
                        mountpath = pathlib.Path(mountpoint)
                        (mountpath / "pagefile.sys").unlink(missing_ok=True)
                        (mountpath / "hiberfil.sys").unlink(missing_ok=True)
                        (mountpath / "swapfile.sys").unlink(missing_ok=True)
                    finally:
                        subprocess.run(["umount", mountpoint], check=True)
                    subprocess.run([args.ntfsclone, "-O", compact_part, orig_part], check=True)
                else:
                    logging.info(f"Cloning {orig_part} -> {compact_part}")
                    subprocess.run(
                        ["dd", f"if={orig_part}", f"of={compact_part}", "bs=1M", "status=progress"], check=True
                    )

            logging.info("Finalizing cloned VDI")
            subprocess.run("sync", check=True)

            myself.disconnect_vdi(compact_vdi)
            vm.connect_vdi(compact_vdi)

            if args.delete:
                logging.info("Deleting original VDI")
                myself.disconnect_vdi(orig_vdi)
                orig_vdi.destroy()


if __name__ == "__main__":
    main()
