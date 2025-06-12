import os

# Requirements:
# From --hosts parameter:
# - A XCP-ng >= 8.2 host

FIRSTBOOT_FILES = [
    'ran-control-domain-params-init',
    'ran-create-guest-templates',
    'ran-generate-iscsi-iqn',
    'ran-network-init',
    'ran-storage-init',
]

FIRSTBOOT_DIR = '/var/lib/misc'

def test_firstboot_ran(host):
    for name in FIRSTBOOT_FILES:
        filepath = os.path.join(FIRSTBOOT_DIR, name)
        assert host.file_exists(filepath)
