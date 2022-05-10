import pytest
from tests.storage import cold_migration_then_come_back, live_storage_migration_then_come_back

# Requirements:
# - running MooseFS cluster
# - access to MooseFS packages repository: ppa.moosefs.com
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.2 with an additional unused disk for the SR.
# - hostA2: Second member of the pool. Can have any local SR. No need to specify it on CLI.
# From --vm parameter
# - A VM to import to the MooseFS SR

@pytest.mark.small_vm # run with a small VM to test the features
@pytest.mark.big_vm # and ideally on a big VM to test it scales
@pytest.mark.usefixtures("hostA2", "local_sr_on_hostA2")
class Test:
    def test_live_intrapool_shared_migration(self, host, hostA2, vm_on_moosefs_sr, moosefs_sr):
        live_storage_migration_then_come_back(vm_on_moosefs_sr, host, moosefs_sr, hostA2, moosefs_sr)

    def test_cold_intrapool_migration(self, host, hostA2, vm_on_moosefs_sr, moosefs_sr, local_sr_on_hostA2):
        cold_migration_then_come_back(vm_on_moosefs_sr, host, moosefs_sr, hostA2, local_sr_on_hostA2)

    def test_live_intrapool_migration(self, host, hostA2, vm_on_moosefs_sr, moosefs_sr, local_sr_on_hostA2):
        live_storage_migration_then_come_back(vm_on_moosefs_sr, host, moosefs_sr, hostA2, local_sr_on_hostA2)
