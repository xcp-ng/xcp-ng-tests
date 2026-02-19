from __future__ import annotations

from data import ARP_SERVER, PXE_CONFIG_SERVER
from lib.commands import scp, ssh

PXE_CONFIG_DIR = "/pxe/configs/custom"

def generate_boot_conf(directory: str, installer: str, action: str) -> None:
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

def server_push_config(mac_address: str, tmp_local_path: str) -> None:
    assert mac_address
    remote_dir = f'{PXE_CONFIG_DIR}/{mac_address}/'
    server_remove_config(mac_address)
    ssh(PXE_CONFIG_SERVER, f'mkdir -p {remote_dir}')
    scp(PXE_CONFIG_SERVER, f'{tmp_local_path}/boot.conf', remote_dir)
    scp(PXE_CONFIG_SERVER, f'{tmp_local_path}/answerfile.xml', remote_dir)

def server_remove_config(mac_address: str) -> None:
    assert mac_address # protection against deleting the whole parent dir!
    remote_dir = f'{PXE_CONFIG_DIR}/{mac_address}/'
    ssh(PXE_CONFIG_SERVER, f'rm -rf {remote_dir}')

def server_remove_bootconf(mac_address: str) -> None:
    assert mac_address
    distant_file = f'{PXE_CONFIG_DIR}/{mac_address}/boot.conf'
    ssh(PXE_CONFIG_SERVER, f'rm -rf {distant_file}')

def arp_addresses_for(mac_address: str) -> list[str]:
    output = ssh(
        ARP_SERVER,
        f"ip neigh show nud reachable | grep {mac_address} | awk '{{ print $1 }}'"
    )
    candidate_ips = output.splitlines()
    return candidate_ips
