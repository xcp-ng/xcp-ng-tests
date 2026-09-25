import pytest

from lib.vdi import ImageFormat
from tests.storage import cold_migration_then_come_back, live_storage_migration_then_come_back

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.2 with a valid lvmohba config.
# - hostA2: Second member of the pool. Can have any local SR. No need to specify it on CLI.
# From --vm parameter
# - A VM to import to the LVM SR

@pytest.mark.small_vm # run with a small VM to test the features
@pytest.mark.big_vm # and ideally with a big VM to test it scales
@pytest.mark.thick_provisioned
class Test:
    def test_live_intrapool_shared_migration(self, host, hostA2, vm_on_lvmohba_sr, image_format: ImageFormat):
        sr = vm_on_lvmohba_sr.get_sr()
        live_storage_migration_then_come_back(vm_on_lvmohba_sr, host, hostA2, sr, image_format)

    def test_cold_intrapool_migration(self, host, hostA2, vm_on_lvmohba_sr, xfs_sr_on_hostA2,
                                      image_format: ImageFormat):
        cold_migration_then_come_back(vm_on_lvmohba_sr, host, hostA2, xfs_sr_on_hostA2, image_format)

    def test_live_intrapool_migration(self, host, hostA2, vm_on_lvmohba_sr, xfs_sr_on_hostA2,
                                      image_format: ImageFormat):
        live_storage_migration_then_come_back(vm_on_lvmohba_sr, host, hostA2, xfs_sr_on_hostA2, image_format)
