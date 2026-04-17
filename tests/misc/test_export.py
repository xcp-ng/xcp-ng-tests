import pytest

import logging

from lib.host import Host
from lib.vm import VM

from typing import Literal

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host with an unused disk to store the exported VM
# From --vm parameter:
# - A VM to import and export

def export_test(host: Host, vm: VM, filepath: str, compress: Literal['none', 'gzip', 'zstd'] = 'none') -> None:
    vm.export(filepath, compress)
    assert host.file_exists(filepath)

    def check_file_type(expected: str) -> None:
        assert host.ssh(f'file --mime-type -b {filepath}') == expected

    if compress == 'none':
        check_file_type('application/x-tar')
    elif compress == 'gzip':
        check_file_type('application/x-gzip')
    elif compress == 'zstd':
        check_file_type('application/octet-stream')
    else:
        assert False, 'Unsupported compress mode'

    vm2 = None
    try:
        vm2 = host.import_vm(filepath)
        vm2.start()
        vm2.wait_for_os_booted()
        vm2.shutdown(verify=True)
    finally:
        logging.info("Delete %s" % filepath)
        host.ssh(f'rm -f {filepath}', check=False)
        if vm2 is not None:
            vm2.destroy()

@pytest.mark.small_vm # run on a small VM to test the functions
@pytest.mark.big_vm # and also on a really big VM ideally to make sure it scales
class TestExport:
    def test_export_zstd(self, host: Host, formatted_and_mounted_ext4_disk: str, imported_vm: VM) -> None:
        filepath = formatted_and_mounted_ext4_disk + '/test-export-zstd.xva'
        export_test(host, imported_vm, filepath, 'zstd')

    def test_export_gzip(self, host: Host, formatted_and_mounted_ext4_disk: str, imported_vm: VM) -> None:
        filepath = formatted_and_mounted_ext4_disk + '/test-export-gzip.xva'
        export_test(host, imported_vm, filepath, 'gzip')

    def test_export_uncompressed(self, host: Host, formatted_and_mounted_ext4_disk: str, imported_vm: VM) -> None:
        filepath = formatted_and_mounted_ext4_disk + '/test-export-uncompressed.xva'
        export_test(host, imported_vm, filepath, 'none')
