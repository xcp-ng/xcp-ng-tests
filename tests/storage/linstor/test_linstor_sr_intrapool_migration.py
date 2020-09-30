import pytest
from lib.common import cold_migration_then_come_back, live_storage_migration_then_come_back

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.2 with an additional unused disk for the SR.
# - hostA2: Second member of the pool. Can have any local SR. No need to specify it on CLI.
# From --vm parameter
# - A VM to import to the LINSTOR SR
# And:
# - access to XCP-ng RPM repository from hostA1
# - a repo with the LINSTOR RPMs must be given using the command line param `--additional-repos`

@pytest.mark.usefixtures("hostA2", "local_sr_on_hostA2")
class Test:
    def test_cold_intrapool_migration(self, host, hostA2, vm_on_linstor_sr, linstor_sr, local_sr_on_hostA2):
        cold_migration_then_come_back(vm_on_linstor_sr, host, linstor_sr, hostA2, local_sr_on_hostA2)

    def test_live_intrapool_migration(self, host, hostA2, vm_on_linstor_sr, linstor_sr, local_sr_on_hostA2):
        live_storage_migration_then_come_back(vm_on_linstor_sr, host, linstor_sr, hostA2, local_sr_on_hostA2)
