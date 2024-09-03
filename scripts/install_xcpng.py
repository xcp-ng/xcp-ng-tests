#!/usr/bin/env python3

import argparse
import atexit
import logging
import os
import random
import requests
import string
import subprocess
import sys
import tempfile
import time

from packaging import version

sys.path.append(f"{os.path.abspath(os.path.dirname(__file__))}/..") # noqa
from lib.commands import ssh, scp, SSHCommandFailed
from lib.common import wait_for, is_uuid
from lib.host import host_data
from lib.pool import Pool
from lib.vm import VM

try:
    from data import PXE_CONFIG_SERVER
    assert PXE_CONFIG_SERVER
except ImportError:
    raise Exception('No address for the PXE server found in data.py (`PXE_CONFIG_SERVER`)')

logging.basicConfig(format='[%(levelname)s] %(message)s', level=logging.INFO)

PXE_CONFIG_DIR = "/pxe/configs/custom"

def generate_boot_conf(directory, installer, action):
    # in case of restore, we disable the text ui from the installer completely,
    # to workaround a bug that leaves us stuck on a confirmation dialog at the end of the operation.
    rt = 'rt=1' if action == 'restore' else ''
    with open(f'{directory}/boot.conf', 'w') as bootfile:
        bootfile.write(f"""
answerfile=custom
installer={installer}
is_default=1
{rt}
""")

def generate_answerfile(directory, installer, hostname_or_ip, target_hostname, action, hdd, netinstall_gpg_check):
    password = host_data(hostname_or_ip)['password']
    cmd = ['openssl', 'passwd', '-6', password]
    res = subprocess.run(cmd, stdout=subprocess.PIPE)
    encrypted_password = res.stdout.decode().strip()
    if target_hostname is None:
        target_hostname = "xcp-ng-" + "".join(
            random.choice(string.ascii_lowercase) for i in range(5)
        )
    with open(f'{directory}/answerfile.xml', 'w') as answerfile:
        if action == 'install':
            answerfile.write(f"""<?xml version="1.0"?>
<installation{netinstall_gpg_check}>
    <keymap>fr</keymap>
    <primary-disk>{hdd}</primary-disk>
    <guest-disk>{hdd}</guest-disk>
    <root-password type="hash">{encrypted_password}</root-password>
    <source type="url">{installer}</source>
    <admin-interface name="eth0" proto="dhcp" />
    <timezone>Europe/Paris</timezone>
    <hostname>{target_hostname}</hostname>
    <script stage="filesystem-populated" type="url">
        http://{PXE_CONFIG_SERVER}/configs/presets/scripts/filesystem-populated.py
    </script>
</installation>
        """)
        elif action == 'upgrade':
            answerfile.write(f"""<?xml version="1.0"?>
<installation mode="upgrade"{netinstall_gpg_check}>
    <existing-installation>{hdd}</existing-installation>
    <source type="url">{installer}</source>
    <script stage="filesystem-populated" type="url">
        http://{PXE_CONFIG_SERVER}/configs/presets/scripts/filesystem-populated.py
    </script>
</installation>
        """)
        elif action == 'restore':
            answerfile.write(f"""<?xml version="1.0"?>
<restore>
</restore>
        """)
        else:
            raise Exception(f"Unknown action: `{action}`")

def server_push_config(mac_address, tmp_local_path):
    assert mac_address
    remote_dir = f'{PXE_CONFIG_DIR}/{mac_address}/'
    server_remove_config(mac_address)
    ssh(PXE_CONFIG_SERVER, ['mkdir', '-p', remote_dir])
    scp(PXE_CONFIG_SERVER, f'{tmp_local_path}/boot.conf', remote_dir)
    scp(PXE_CONFIG_SERVER, f'{tmp_local_path}/answerfile.xml', remote_dir)

def server_remove_config(mac_address):
    assert mac_address # protection against deleting the whole parent dir!
    remote_dir = f'{PXE_CONFIG_DIR}/{mac_address}/'
    ssh(PXE_CONFIG_SERVER, ['rm', '-rf', remote_dir])

def server_remove_bootconf(mac_address):
    assert mac_address
    distant_file = f'{PXE_CONFIG_DIR}/{mac_address}/boot.conf'
    try:
        ssh(PXE_CONFIG_SERVER, ['rm', '-rf', distant_file])
    except SSHCommandFailed as e:
        raise Exception('ERROR: failed to clean the boot.conf file.' + e)

def arp_addresses_for(mac_address):
    output = ssh(
        PXE_CONFIG_SERVER,
        ['arp', '-n', '|', 'grep', mac_address, '|', 'awk', '\'{ print $1 }\'']
    )
    candidate_ips = output.splitlines()
    return candidate_ips

def is_ip_active(ip):
    return not os.system(f"ping -c 3 -W 10 {ip} > /dev/null 2>&1")

def is_ssh_up(ip):
    try:
        ssh(ip, ['true'], options=['-o "ConnectTimeout 10"'])
        return True
    except SSHCommandFailed:
        # probably not up yet
        return False

def get_new_host_ip(mac_address):
    candidate_ips = arp_addresses_for(mac_address)
    logging.debug("Candidate IPs: " + ", ".join(candidate_ips))
    for ip in candidate_ips:
        if is_ip_active(ip) and is_ssh_up(ip):
            return ip
    return None

def is_new_host_ready(ip_address):
    try:
        output = ssh(ip_address, ['xe', 'host-list', 'enabled=true', '--minimal'])
        return is_uuid(output)
    except Exception:
        return False

def check_mac_address(host, mac_address):
    bridge = host.inventory['MANAGEMENT_INTERFACE']
    host_mac_address = host.ssh(['cat', f'/sys/class/net/{bridge}/address'])
    if mac_address != host_mac_address:
        raise Exception(
            f"Unexpected MAC address `{host_mac_address}` for host `{host.hostname_or_ip}`. "
            f"Expected: `{mac_address}`"
        )

def url_checker(url):
    try:
        response = requests.get(url)
        if not response:
            raise Exception(f"{url}: URL is not reachable, status_code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        raise SystemExit(f"{url}: URL is not reachable\nErr: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "host",
        help="hostname or IP address of the host hosting the VM that will be installed/upgraded/restored"
    )
    parser.add_argument("vm_uuid", help="UUID of an existing VM in which XCP-ng will be installed/upgraded/restored")
    parser.add_argument(
        "action", metavar='action', choices=['install', 'upgrade', 'restore'], help="install, upgrade or restore"
    )
    parser.add_argument(
        "xcpng_version",
        help="target version, used to build the installer URL if none provided via --installer, "
             "and also used to check the system version at the end. Example: 8.2.1"
             "In case of a restore, specify the version of the installer."
    )
    parser.add_argument("--installer", help="URL of the installer")
    parser.add_argument(
        "-t", "--target-hostname",
        help="The hostname of the VM in which XCP-ng will be installed. By default "
             "a hostname is generated starting with xcp-ng-XXXXX where XXXXX is "
             "randomly generated using lowercase characters.")
    parser.add_argument("--netinstall-gpg-check", default=False, action='store_true', help="Disable GPG Check")
    args = parser.parse_args()

    # *** "fail early" checks

    if not is_uuid(args.vm_uuid):
        raise Exception(f'The provided VM UUID is invalid: {args.vm_uuid}')

    if args.xcpng_version[0].isdigit():
        xcp_version = args.xcpng_version
    else:
        raise Exception(f'The version does not seem valid: {args.xcpng_version}')

    if args.netinstall_gpg_check:
        netinstall_gpg_check = " netinstall-gpg-check=\"false\""
    else:
        netinstall_gpg_check = ""

    # *** slower checks (involving network, SSH...)

    if not is_ssh_up(args.host):
        raise Exception(f"Could not SSH into host `{args.host}`")

    try:
        pool = Pool(args.host) # will fail if host is not XCP-ng or XAPI doesn't respond yet
    except Exception as e:
        raise Exception(f"Host `{args.host}` isn't ready or isn't an XCP-ng host")

    host = pool.master
    assert host.is_enabled()

    if not args.installer:
        installer = f"http://{PXE_CONFIG_SERVER}/installers/xcp-ng/{xcp_version}/"
    else:
        installer = args.installer

    try:
        url_checker(f"{installer}.treeinfo")
    except Exception:
        raise Exception(f"No installer found at URL `{installer}`")

    vm = VM(args.vm_uuid, host)
    vif = vm.vifs()[0]
    mac_address = vif.param_get('MAC')
    with tempfile.TemporaryDirectory(suffix=mac_address) as tmp_local_path:
        logging.info('Generate files: answerfile.xml and boot.conf')
        hdd = 'nvme0n1' if vm.is_uefi else 'sda'
        generate_answerfile(tmp_local_path, installer, args.host, args.target_hostname, args.action, hdd,
                            netinstall_gpg_check)
        generate_boot_conf(tmp_local_path, installer, args.action)
        logging.info('Copy files to the pxe server')
        server_push_config(mac_address, tmp_local_path)
        atexit.register(lambda: server_remove_config(mac_address))
        if (vm.is_running()):
            try:
                vm.shutdown(verify=True)
            except Exception:
                vm.shutdown(force=True, verify=True)
        vm.start()
        # wait a bit to let the PXE server give the boot configuration to the VM, then disable the specific boot config
        time.sleep(20)
        server_remove_bootconf(mac_address)
        wait_for(
            lambda: get_new_host_ip(mac_address) is not None,
            "Waiting for the installation process to complete and the VM to reboot and be up", 3600, 10
        )
        vm_ip_address = get_new_host_ip(mac_address)
        logging.info('The IP address of the installed XCP-ng is: ' + vm_ip_address)
        wait_for(lambda: is_new_host_ready(vm_ip_address), "Waiting for XAPI to be ready", 600, 10)
        pool2 = Pool(vm_ip_address)
        host2 = pool2.master
        host2.inventory = host2._get_xensource_inventory()
        check_mac_address(host2, mac_address)
        logging.info(f'Target host is started and enabled in version: {host2.xcp_version}')
        if args.action == 'restore' and host2.xcp_version >= version.parse(xcp_version):
            raise Exception(
                f"The installed host ({vm_ip_address}) is not in a previous version. Got: {host2.xcp_version}.\n"
            )
        elif args.action != 'restore' and host2.xcp_version != version.parse(xcp_version):
            raise Exception(
                f"The installed host ({vm_ip_address}) is not in the expected version. Got: {host2.xcp_version}.\n"
                f"Expected: {xcp_version}."
            )

if __name__ == '__main__':
    main()
