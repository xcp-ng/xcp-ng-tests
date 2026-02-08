import pytest

from lib.host import Host
from lib.sr import SR
from lib.vm import VM
from tests.storage import cold_migration_then_come_back, live_storage_migration_then_come_back

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.0 with an additional unused disk for the SR.
# - hostA2: Second member of the pool. Can have any local SR. No need to specify it on CLI.
# From --vm parameter
# - A VM to import to the NFS SR

@pytest.mark.small_vm # run with a small VM to test the features
@pytest.mark.big_vm # and ideally with a big VM to test it scales
@pytest.mark.usefixtures("hostA2", "local_sr_on_hostA2")
# Make sure these fixtures are called before the parametrized one
@pytest.mark.usefixtures('vm_ref')
@pytest.mark.usefixtures('image_format')
class Test:
    @pytest.mark.parametrize('dispatch_nfs', ['vm_on_nfs_sr', 'vm_on_nfs4_sr'], indirect=True)
    def test_live_intrapool_shared_migration(self, host: Host, hostA2: Host, dispatch_nfs: VM) -> None:
        sr = dispatch_nfs.get_sr()
        live_storage_migration_then_come_back(dispatch_nfs, host, hostA2, sr)

    @pytest.mark.parametrize('dispatch_nfs', ['vm_on_nfs_sr', 'vm_on_nfs4_sr'], indirect=True)
    def test_cold_intrapool_migration(self, host: Host, hostA2: Host, dispatch_nfs: VM, local_sr_on_hostA2: SR) -> None:
        cold_migration_then_come_back(dispatch_nfs, host, hostA2, local_sr_on_hostA2)

    @pytest.mark.parametrize('dispatch_nfs', ['vm_on_nfs_sr', 'vm_on_nfs4_sr'], indirect=True)
    def test_live_intrapool_migration(self, host: Host, hostA2: Host, dispatch_nfs: VM, local_sr_on_hostA2: SR) -> None:
        live_storage_migration_then_come_back(dispatch_nfs, host, hostA2, local_sr_on_hostA2)
