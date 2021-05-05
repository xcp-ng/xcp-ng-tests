import pytest

from lib.efi import (
    EFI_RT_ATTRS,
    EFI_RT_ATTRS_BYTES,
)

# Requirements:
# - xcp-ng-test tools/efivar submodule has been built and the
#   tools/efivar-static executable exists
# - VM is a UEFI Linux VM

TEST_GUID = '8be4df61-93ca-11d2-aa0d-000000000001'

class TestGuestLinuxUEFIVariable:
    @pytest.fixture(autouse=True)
    def setup_and_cleanup(self, running_vm):
        vm = running_vm
        if vm.is_windows:
            pytest.skip('only valid for Linux VMs')

        snapshot = vm.snapshot()
        yield

        try:
            snapshot.revert()
        except subprocess.CalledProcessError:
            raise
        finally:
            snapshot.destroy()

    def test_append(self, running_vm):
        """Test variable appends."""
        vm = running_vm

        # Create a variable KingOfTheHill with value set to 'hank'
        vm.set_efi_var(
            'KingOfTheHill',
            TEST_GUID,
            EFI_RT_ATTRS_BYTES,
            b'hank'
        )

        # Use efivar to append 'hill' to variable KingOfTheHill
        vm.create_file('/tmp/hill.data', b'hill')
        name = '%s-%s' % (TEST_GUID, 'KingOfTheHill')
        vm.execute_bin('tools/efivar-static',
                       ['-n', name, '--append', '-f', '/tmp/hill.data'])

        # Assert that new value of KingOfTheHill is 'hankhill'
        attrs, data = vm.get_efi_var('KingOfTheHill', TEST_GUID)
        assert attrs == EFI_RT_ATTRS_BYTES
        assert data == b'hankhill'
