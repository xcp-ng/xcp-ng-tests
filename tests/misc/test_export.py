import logging
import pytest
from packaging import version

# Requirements:
# From --hosts parameter:
# - host: a XCP-ng host
# From --sr-disk parameter:
# - an additional unused disk to store the exported VM
# From --vm parameter:
# - A VM to import and export

def export_test(host, vm, filepath, compress='none'):
    vm.export(filepath, compress)
    assert host.file_exists(filepath)

    def check_file_type(expected):
        assert host.ssh(['file', '--mime-type', '-b', filepath]) == expected

    if compress == 'none':
        check_file_type('application/x-tar')
    elif compress == 'gzip':
        if host.xcp_version > version.parse("8.3"):
            check_file_type('application/gzip')
        else:
            check_file_type('application/x-gzip')
    elif compress == 'zstd':
        if host.xcp_version > version.parse("8.3"):
            check_file_type('application/zstd')
        else:
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
        host.ssh(['rm', '-f', filepath], check=False)
        if vm2 is not None:
            vm2.destroy()

@pytest.mark.small_vm # run on a small VM to test the functions
@pytest.mark.big_vm # and also on a really big VM ideally to make sure it scales
class TestExport:
    def test_export_zstd(self, host, formatted_and_mounted_ext4_disk, imported_vm):
        filepath = formatted_and_mounted_ext4_disk + '/test-export-zstd.xva'
        export_test(host, imported_vm, filepath, 'zstd')

    def test_export_gzip(self, host, formatted_and_mounted_ext4_disk, imported_vm):
        filepath = formatted_and_mounted_ext4_disk + '/test-export-gzip.xva'
        export_test(host, imported_vm, filepath, 'gzip')

    def test_export_uncompressed(self, host, formatted_and_mounted_ext4_disk, imported_vm):
        filepath = formatted_and_mounted_ext4_disk + '/test-export-uncompressed.xva'
        export_test(host, imported_vm, filepath, 'none')
