from lib.commands import ssh, scp

PXE_CONFIG_DIR = "/pxe/configs/custom"

try:
    from data import PXE_CONFIG_SERVER
    assert PXE_CONFIG_SERVER
except ImportError:
    raise Exception('No address for the PXE server found in data.py (`PXE_CONFIG_SERVER`)')

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
    ssh(PXE_CONFIG_SERVER, ['rm', '-rf', distant_file])

def arp_addresses_for(mac_address):
    output = ssh(
        PXE_CONFIG_SERVER,
        ['arp', '-n', '|', 'grep', mac_address, '|', 'awk', '\'{ print $1 }\'']
    )
    candidate_ips = output.splitlines()
    return candidate_ips
