import pytest
from tests.storage import cold_migration_then_come_back, live_storage_migration_then_come_back

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.2 with an additional unused disk for the SR.
# - hostB1: Master of a second pool. Any local SR.
# From --vm parameter
# - A VM to import to the LINSTOR SR
# And:
# - access to XCP-ng RPM repository from hostA1

@pytest.mark.small_vm # run with a small VM to test the features
@pytest.mark.big_vm # and ideally on a big VM to test it scales
@pytest.mark.usefixtures("hostB1", "hosts_with_xo", "local_sr_on_hostB1")
class Test:
    def test_cold_crosspool_migration(self, host, hostB1, vm_on_linstor_sr, linstor_sr, local_sr_on_hostB1):
        cold_migration_then_come_back(vm_on_linstor_sr, host, linstor_sr, hostB1, local_sr_on_hostB1)

    def test_live_crosspool_migration(self, host, hostB1, vm_on_linstor_sr, linstor_sr, local_sr_on_hostB1):
        live_storage_migration_then_come_back(vm_on_linstor_sr, host, linstor_sr, hostB1, local_sr_on_hostB1)
