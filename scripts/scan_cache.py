#!/usr/bin/env python3

import argparse
import contextlib
import getpass
import itertools
import pathlib
import re
import sys

import XenAPI  # type: ignore[import-untyped]

from typing import Any, cast

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# flake8: noqa: E402 module level import not at top of file
from lib.typing import VmDef, VMSDef

# Scan the current pool for test images that are not described in vm_data.py.
# Deleting them is up to the user.


@contextlib.contextmanager
def xapi_session(uname="root", pwd="", url=None, ignore_ssl=False) -> Any:
    if url is None:
        session: Any = XenAPI.xapi_local()
    else:
        session = XenAPI.Session(url, ignore_ssl=ignore_ssl)
    session.xenapi.login_with_password(uname, pwd, XenAPI.API_VERSION_1_2, "scan_cache.py")
    try:
        yield session
    finally:
        session.xenapi.logout()


def strip_suffix(string: str, suffix: str) -> str:
    if string.endswith(suffix):
        return string[: -len(suffix)]
    return string


def get_cache_url(vm: VmDef) -> str:
    if isinstance(vm, tuple):
        url = vm[1]
    else:
        url = vm
    return strip_suffix(url, '.xva')


def get_image_urls() -> set[Any]:
    from vm_data import VMS as VMS_untyped

    VMS = cast(VMSDef, VMS_untyped)
    image_urls = set()
    for vm in dict(VMS["single"]).values():
        image_urls.add(get_cache_url(vm))
    for vms in dict(VMS["multi"]).values():
        for vm in vms:
            image_urls.add(get_cache_url(vm))
    return image_urls


def get_vm_type(session: Any, vm_ref: str) -> str:
    vm_type = "VM"
    if session.xenapi.VM.get_is_control_domain(vm_ref):
        vm_type = "control domain"
    elif session.xenapi.VM.get_is_default_template(vm_ref):
        vm_type = "default template"
    elif session.xenapi.VM.get_is_a_snapshot(vm_ref):
        vm_type = "snapshot"
    elif session.xenapi.VM.get_is_a_template(vm_ref):
        vm_type = "template"
    return vm_type


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--hosts",
        action="append",
        required=True,
        help="list of hosts to scan (comma-separated)",
    )
    parser.add_argument(
        "--uname",
        default="root",
        help="user name for connecting to XAPI",
    )
    parser.add_argument(
        "--pwd",
        action="store_true",
        help="prompt for password",
    )
    args = parser.parse_args()

    pwd = ""
    if args.pwd:
        pwd = getpass.getpass()

    # a list of master hosts, each from a different pool
    hosts_args = args.hosts
    hosts_split = [hostlist.split(',') for hostlist in hosts_args]
    hostname_list = list(itertools.chain(*hosts_split))

    image_urls = get_image_urls()

    for host in hostname_list:
        with xapi_session(args.uname, pwd, url=f"https://{host}", ignore_ssl=True) as session:
            for vm_ref in session.xenapi.VM.get_all():
                if get_vm_type(session, vm_ref) != "VM":
                    continue
                description = session.xenapi.VM.get_name_description(vm_ref)
                m = re.match(r'\[Cache for (.*)\]', description)
                if not m:
                    continue
                current_url = m.group(1)
                if current_url in image_urls:
                    continue
                uuid = session.xenapi.VM.get_uuid(vm_ref)
                label = session.xenapi.VM.get_name_label(vm_ref)
                print(f"{uuid} {label} {description}")


if __name__ == "__main__":
    main()
