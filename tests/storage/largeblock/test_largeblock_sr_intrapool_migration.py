import pytest

from lib.host import Host
from lib.sr import SR
from lib.vm import VM
from tests.storage import cold_migration_then_come_back, live_storage_migration_then_come_back

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host with an additional unused 4KiB disk for the largeblock SR.
# - hostA2: Second member of the pool. Can have any local SR. No need to specify it on CLI.
# From --vm parameter
# - A VM to import to the EXT SR

@pytest.mark.small_vm # run with a small VM to test the features
@pytest.mark.big_vm # and ideally with a big VM to test it scales
@pytest.mark.usefixtures("hostA2", "local_sr_on_hostA2")
class Test:
    def test_cold_intrapool_migration(
        self, host: Host, hostA2: Host, vm_on_largeblock_sr: VM, local_sr_on_hostA2: SR
    ) -> None:
        cold_migration_then_come_back(vm_on_largeblock_sr, host, hostA2, local_sr_on_hostA2)

    def test_live_intrapool_migration(
        self, host: Host, hostA2: Host, vm_on_largeblock_sr: VM, local_sr_on_hostA2: SR
    ) -> None:
        live_storage_migration_then_come_back(vm_on_largeblock_sr, host, hostA2, local_sr_on_hostA2)
