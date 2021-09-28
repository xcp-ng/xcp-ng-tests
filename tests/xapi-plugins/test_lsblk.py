def test_list_block_devices(host):
    host.call_plugin('lsblk.py', 'list_block_devices')
