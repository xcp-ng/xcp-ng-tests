#!/usr/bin/env python

import argparse
import atexit
import logging
import os
import sys
import tempfile
from signal import SIGABRT, SIGINT, SIGQUIT, SIGTERM, signal
from subprocess import run

import requests
import yaml
from bs4 import BeautifulSoup

import __main__

from typing import List

try:
    from yaml import CDumper as Dumper
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Dumper, Loader

cur = os.path.split(__main__.__file__)[0]
root = os.path.abspath(os.path.join(cur, "..", ".."))
sys.path.append(root)

import data  # noqa
from lib.commands import ssh  # noqa
from lib.pool import Pool # noqa
from lib.vm import VM # noqa

user = getattr(data, "HOST_DEFAULT_USER", "root")


def get_url_paths(url, ext="", params={}):
    """Return all paths of files found at http server at url that match extension ext."""
    response = requests.get(url, params=params)
    if response.ok:
        response_text = response.text
    else:
        return response.raise_for_status()
    soup = BeautifulSoup(response_text, "html.parser")
    return [url + node.get("href") for node in soup.find_all("a") if node.get("href").endswith(ext)]


class Image:
    def __init__(self, url):
        self.url = url
        self.image_name = os.path.basename(self.url)
        self.ansible_hostname = self.image_name.replace("-", "_")
        self.export_path = os.path.join(args.export_directory, self.image_name)
        self.vm = None

    @staticmethod
    def from_http_server(server_url):
        if server_url[-1] != "/":
            server_url += "/"

        return [Image(url) for url in get_url_paths(server_url, ".xva")]

    def __str__(self):
        return self.url


def get_ansible_hosts(playbook: str) -> List[str]:
    ansible_hosts = []
    with open(playbook, "r") as f:
        for play in yaml.load(f, Loader=Loader):
            ansible_hosts.append(play["hosts"])

    if not ansible_hosts:
        logger.error("No playbook hosts found")
        sys.exit(1)

    logger.info(f"Playbook Hosts Found: {ansible_hosts}")

    return ansible_hosts


def print_images(images: List[Image]):
    print("\nAvailable Images:")
    for image in images:
        print(image)


vms: List[VM] = []


def cleanup(*args, **kwargs):
    if not vms:
        return

    logger.debug(f"Running cleanup")
    while vms:
        vm = vms.pop()
        logger.debug(f"Destroying {vm}")
        vm.destroy()


def modified(host: Host, image: Image) -> bool:
    """
    Return True if the playbook is modified. Otherwise, return False.

    A playbook is considered modified if its modified timestamp is newer than
    the modified timestamp of the image at the export path.

    This avoids updating XVAs that are already up-to-date.
    """
    playbook_modified = int(os.stat(args.playbook).st_mtime)

    if not host.file_exists(image.export_path):
        return True

    image_modified = int(host.ssh(["stat", "--format", "%Y", image.export_path]))
    if image_modified > playbook_modified:
        logger.info((
            f"Skipping {image.image_name}, the file found at "
            f"{image.export_path} is newer than the playbook"
        ))
        return False

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a playbook for updating test VMs")
    parser.add_argument(
        "--http",
        type=str,
        required=False,
        default=getattr(data, "DEF_VM_URL", "http://pxe/images/"),
        help=(
            "The HTTP/PXE server containing the test VMs. Defaults to DEF_VM_URL "
            "in data.py, if not found then defaults to http://pxe/images/"
        ),
    )
    parser.add_argument(
        "--forced",
        action="store_true",
        help="Force update all VMs. Don't skip any due to modified timestamps."
    )
    parser.add_argument(
        "--loglevel",
        choices=["DEBUG", "ERROR", "WARNING", "INFO"],
        default="INFO",
        help="Force update all VMs. Don't skip any due to modified timestamps."
    )
    parser.add_argument(
        "--print-images",
        action="store_true",
        required=False,
        help="Show the available images on the PXE server and then exit.",
    )

    # Args host/playbook are not required if the user only wants to print the images
    if "--print-images" not in sys.argv:
        parser.add_argument(
            "--host",
            "-x",
            type=str,
            help="The XCP-ng hostname or IP address. Defaults to host found in data.py",
        )
        parser.add_argument(
            "--export-directory",
            "-e",
            type=str,
            required=False,
            default=os.path.join("/", "root", "ansible-updates/"),
            help=(
                "The directory on the XCP-ng host to export the updated image. "
                "Defaults to /root/ansible-updates/"
            ),
        )
        parser.add_argument("playbook", type=str, help="The Ansible playbook.")

    args = parser.parse_args()

    logger = logging.getLogger("ansible:runner")
    logger.setLevel(level=getattr(logging, args.loglevel))

    if not logger.hasHandlers():
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
        logger.addHandler(ch)

    atexit.register(cleanup)
    for sig in [SIGINT, SIGQUIT, SIGABRT, SIGTERM]:
        signal(sig, cleanup)

    if args.print_images:
        print_images(images)
        sys.exit(0)

    ansible_hosts = get_ansible_hosts(args.playbook)

    # Initialize the hosts and gather the urls for the VM images to update
    host_ip_or_name = args.host if args.host else list(data.HOSTS.keys())[0]
    pool = Pool(host)
    host = pool.master
    host.initialize()

    # Create an inventory (i.e., hosts file) for Ansible. The host name is the image name.
    lines = []

    images = []
    for image in Image.from_http_server(args.http):
        if image.ansible_hostname not in ansible_hosts:
            continue

        if args.forced or modified(host, image):
            images.append(image)

    for image in images:
        vm = image.vm = host.import_vm(image.url)
        vm.start()
        vm.wait_for_os_booted()
        vms.append(vm)

        lines.append(f"[{image.ansible_hostname}]")
        lines.append(
            f"{vm.ip} ansible_user=root ansible_python_interpreter=auto_silent"
        )
        lines.append("\n")

    if not vms:
        logger.info("No hostnames needing update found in playbook")
        sys.exit(0)

    _, inventory = tempfile.mkstemp()
    with open(inventory, "w") as f:
        logger.info("Generated Inventory:")
        for line in lines:
            logger.info(line)
            f.write(line + "\n")

    # Update the VMs
    run(["ansible-playbook", "-i", inventory, args.playbook])

    # Export the updated VMs
    exports = []
    for image in images:
        try:
            vm = image.vm
            if vm is None:
                logger.warning(f"VM for {image.image_name} not found, not exporting")
                continue
            vm.shutdown(verify=True)
            path = image.export_path
            vm.host.ssh(["mkdir", "-p", os.path.split(path)[0]])
            vm.host.ssh(["rm", "-f", path])
            vm.export(path)
            exports.append(f"{user}@{vm.host.hostname_or_ip}:" + path)
        except Exception as e:
            logger.error(e)

    if exports:
        logger.info("\nExports:")
        logger.info("\n\t".join(exports))
