# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host > 8.2.

def test_list_block_devices(host):
    host.call_plugin('lsblk.py', 'list_block_devices')
