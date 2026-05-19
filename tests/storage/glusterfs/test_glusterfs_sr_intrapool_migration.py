import pytest

from lib import config
from lib.host import Host
from lib.sr import SR
from lib.vdi import ImageFormat
from lib.vm import VM
from tests.storage import cold_migration_then_come_back, live_storage_migration_then_come_back

from typing import Generator

# Requirements:
# From --hosts parameter:
# - host(A1): First XCP-ng host >= 8.2 with an additional unused disk for the glusterfs SR
# - hostA2: Second member of the pool, also requires an aditional unused disk for the glusterfs backup
# From --vm parameter
# - A VM to import to the GlusterFS SR
# And:
# - access to XCP-ng RPM repository from hostA1


@pytest.fixture(scope='package')
def nfs_sr(host: Host, image_format: ImageFormat) -> Generator[SR, None, None]:
    """
    A NFS SR on first host.

    Note: this fixture is already present in `tests/storage/nfs`, but it's scoped to the `nfs` package
    """
    nfs_device_config = config.sr_device_config("NFS_DEVICE_CONFIG") | {'preferred-image-formats': image_format}
    sr = host.sr_create('nfs', "NFS-SR-test", nfs_device_config, shared=True)
    yield sr
    sr.destroy()


@pytest.mark.small_vm  # run with a small VM to test the features
@pytest.mark.big_vm  # and ideally with a big VM to test it scales
class Test:
    def test_live_intrapool_shared_migration(self, host: Host, hostA2: Host, vm_on_glusterfs_sr: VM) -> None:
        sr = vm_on_glusterfs_sr.get_sr()
        live_storage_migration_then_come_back(vm_on_glusterfs_sr, host, hostA2, sr)

    def test_cold_intrapool_migration(self, host: Host, hostA2: Host, vm_on_glusterfs_sr: VM, nfs_sr: SR) -> None:
        cold_migration_then_come_back(vm_on_glusterfs_sr, host, hostA2, nfs_sr)

    def test_live_intrapool_migration(self, host: Host, hostA2: Host, vm_on_glusterfs_sr: VM, nfs_sr: SR) -> None:
        live_storage_migration_then_come_back(vm_on_glusterfs_sr, host, hostA2, nfs_sr)
