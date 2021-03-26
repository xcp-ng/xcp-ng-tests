import pytest

pytestmark = pytest.mark.default_vm("mini-linux-x86_64-bios")

# What can be improved: control over where the exported files get written
# and over the destination SR for VM import.


def export_test(host, vm, filepath, compress="false"):
    vm.export(filepath, compress)
    assert host.file_exists(filepath)
    vm2 = None
    try:
        vm2 = host.import_vm(filepath)
        vm2.start()
        vm2.wait_for_os_booted()
        vm2.shutdown(verify=True)
    finally:
        print("Delete %s" % filepath)
        host.ssh(["rm", "-f", filepath], check=False)
        if vm2 is not None:
            vm2.destroy()


def test_export_zstd(host, imported_vm):
    export_test(host, imported_vm, "/root/test-export-zstd.xva", "zstd")


def test_export_gzip(host, imported_vm):
    export_test(host, imported_vm, "/root/test-export-gzip.xva", "true")


def test_export_uncompressed(host, imported_vm):
    export_test(host, imported_vm, "/root/test-export-uncompressed.xva", "false")
