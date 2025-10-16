import pytest

from tests.storage import cold_migration_then_come_back, live_storage_migration_then_come_back

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.0 with an additional unused disk for the SR.
# - hostB1: Master of a second pool. Any local SR.
# From --vm parameter
# - A VM to import to the NFS SR

@pytest.mark.small_vm # run with a small VM to test the features
@pytest.mark.big_vm # and ideally with a big VM to test it scales
@pytest.mark.usefixtures("hostB1", "local_sr_on_hostB1")
# Make sure these fixtures are called before the parametrized one
@pytest.mark.usefixtures('vm_ref')
class Test:
    @pytest.mark.usefixtures('image_format')
    @pytest.mark.parametrize('dispatch_nfs', ['vm_on_nfs_sr', 'vm_on_nfs4_sr'], indirect=True)
    def test_cold_crosspool_migration(self, host, hostB1, dispatch_nfs, local_sr_on_hostB1):
        cold_migration_then_come_back(dispatch_nfs, host, hostB1, local_sr_on_hostB1)

    @pytest.mark.usefixtures('image_format')
    @pytest.mark.parametrize('dispatch_nfs', ['vm_on_nfs_sr', 'vm_on_nfs4_sr'], indirect=True)
    def test_live_crosspool_migration(self, host, hostB1, dispatch_nfs, local_sr_on_hostB1):
        live_storage_migration_then_come_back(dispatch_nfs, host, hostB1, local_sr_on_hostB1)
