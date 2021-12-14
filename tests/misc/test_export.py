import logging

# What can be improved: control over where the exported files get written
# and over the destination SR for VM import.

def export_test(host, vm, filepath, compress='none'):
    vm.export(filepath, compress)
    assert host.file_exists(filepath)

    def check_file_type(expected):
        assert host.ssh(['file', '--mime-type', '-b', filepath]) == expected

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
        host.ssh(['rm', '-f', filepath], check=False)
        if vm2 is not None:
            vm2.destroy()

def test_export_zstd(host, imported_vm):
    export_test(host, imported_vm, '/root/test-export-zstd.xva', 'zstd')

def test_export_gzip(host, imported_vm):
    export_test(host, imported_vm, '/root/test-export-gzip.xva', 'gzip')

def test_export_uncompressed(host, imported_vm):
    export_test(host, imported_vm, '/root/test-export-uncompressed.xva', 'none')
