import pytest
from lib.common import cold_migration_then_come_back, live_storage_migration_then_come_back

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.0 with an additional unused disk for the SR.
# - hostB1: Master of a second pool. Any local SR.
# From --vm parameter
# - A VM to import to the NFS SR

@pytest.mark.usefixtures("hostB1", "local_sr_on_hostB1")
class Test:
    def test_cold_crosspool_migration(self, host, hostB1, vm_on_nfs_sr, nfs_sr, local_sr_on_hostB1):
        cold_migration_then_come_back(vm_on_nfs_sr, host, nfs_sr, hostB1, local_sr_on_hostB1)

    def test_live_crosspool_migration(self, host, hostB1, vm_on_nfs_sr, nfs_sr, local_sr_on_hostB1):
        live_storage_migration_then_come_back(vm_on_nfs_sr, host, nfs_sr, hostB1, local_sr_on_hostB1)
