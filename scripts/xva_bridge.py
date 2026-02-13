#!/usr/bin/env python3

# Tested on libarchive-c==5.3. Due to our use of library internals, may not work on other versions of libarchive-c.

import argparse
import io
import logging
import os
from xml.dom import minidom

import libarchive  # type: ignore
import libarchive.ffi  # type: ignore

from typing import Generator

class XvaHeaderMember:
    def __init__(self, member: minidom.Element):
        self.member = member

    def get_name(self) -> str | None:
        for child in self.member.childNodes:
            if isinstance(child, minidom.Element) and child.tagName == "name" and child.firstChild:
                return child.firstChild.nodeValue
        return None

    def get_value(self) -> str | None:
        for child in self.member.childNodes:
            if isinstance(child, minidom.Element) and child.tagName == "value" and child.firstChild:
                return child.firstChild.nodeValue
        return None

    def set_value(self, value: str) -> None:
        for child in self.member.childNodes:
            if isinstance(child, minidom.Element) and child.tagName == "value" and child.firstChild:
                child.firstChild.nodeValue = value  # type: ignore
        return None


class XvaHeader:
    def __init__(self, header_bytes: bytes):
        self.xml = minidom.parseString(header_bytes.decode())

    def members(self) -> Generator[XvaHeaderMember, None, None]:
        for member in self.xml.getElementsByTagName("member"):
            if member.nodeType == minidom.Node.ELEMENT_NODE:
                yield XvaHeaderMember(member)

    def get_bridge(self) -> str:
        for member in self.members():
            if member.get_name() == "bridge":
                v = member.get_value()
                assert v is not None
                return v
        raise ValueError("Could not find bridge value in XVA header")

    def set_bridge(self, bridge: str) -> None:
        for member in self.members():
            if member.get_name() == "bridge":
                member.set_value(bridge)
                return
        raise ValueError("Could not find bridge value in XVA header")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("xva", help="input file path")
    parser.add_argument(
        "--set-bridge", help="new bridge value of format `xenbr0|xapi[:9]|...`; omit this option to show current bridge"
    )
    parser.add_argument(
        "--compression",
        choices=["zstd", "gzip"],
        default="zstd",
        help="compression mode of new XVA when setting bridge value (default: zstd)",
    )
    parser.add_argument("-o", "--output", help="output file path (must not be the same as input)")
    parser.add_argument("--backup-path", help="backup file path")
    parser.add_argument(
        "--in-place", action="store_true", help="rename output file to input file; rename input file to backup file"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)

    with libarchive.file_reader(args.xva, "tar") as input_file:
        logging.debug(f"Compression: {', '.join(filter.decode() for filter in input_file.filter_names)}")

        entry_iter = iter(input_file)

        header_entry = next(entry_iter)
        if header_entry.pathname != "ova.xml":
            raise ValueError("Unexpected header entry name")
        with io.BytesIO() as header_writer:
            for block in header_entry.get_blocks():
                header_writer.write(block)
            header_bytes = header_writer.getvalue()

        logging.debug(f"Header is {len(header_bytes)} bytes")

        header = XvaHeader(header_bytes)
        bridge = header.get_bridge()
        logging.info(f"Found bridge {bridge}")

        if args.set_bridge:
            output_path = args.output
            if not output_path:
                output_path = args.xva + ".new"
            logging.info(f"Output path: {output_path}")

            logging.info(f"Setting bridge to {args.set_bridge}")
            header.set_bridge(args.set_bridge)

            logging.debug(f"Using compression {args.compression}")
            with libarchive.file_writer(output_path, "pax_restricted", args.compression) as output_file:
                new_header_bytes = header.xml.toxml().encode()
                output_file.add_file_from_memory(
                    "ova.xml", len(new_header_bytes), new_header_bytes, permission=0o400, uid=0, gid=0
                )

                for entry in entry_iter:
                    logging.debug(f"Copying {entry.pathname}: {entry.size} bytes")
                    new_entry = libarchive.ArchiveEntry(entry.header_codec, perm=0o400, uid=0, gid=0)
                    for attr in ["filetype", "pathname", "size"]:
                        setattr(new_entry, attr, getattr(entry, attr))

                    # ArchiveEntry doesn't expose block copying, so write the entry manually via the FFI interface
                    libarchive.ffi.write_header(output_file._pointer, new_entry._entry_p)  # type: ignore # noqa: SLF001
                    for block in entry.get_blocks():
                        libarchive.ffi.write_data(output_file._pointer, block, len(block)) # type: ignore # noqa: SLF001
                    libarchive.ffi.write_finish_entry(output_file._pointer)  # type: ignore  # noqa: SLF001

            if args.in_place:
                backup_path = args.backup_path
                if not backup_path:
                    backup_path = args.xva + ".bak"
                logging.info(f"Backup path: {backup_path}")

                logging.info(f"Renaming {args.xva} -> {backup_path}")
                os.rename(args.xva, backup_path)
                logging.info(f"Renaming {output_path} -> {args.xva}")
                os.rename(output_path, args.xva)
