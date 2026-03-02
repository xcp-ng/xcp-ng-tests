import pytest

from lib.host import Host

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.2.
# - hostB1: Master of a second pool, same version as hostA1.
#   This host will be joined and ejected from pool A, it means its state will be completely reinitialized from scratch

@pytest.mark.reboot
def test_pool_join(hostA1: Host, hostB1: Host) -> None:
    hostB1.join_pool(hostA1.pool)
    # FIXME: is this gymnastics necessary?
    # If no, fix TestPoolToDiskCertInheritanceOnPoolJoin too
    joined_host = hostA1.pool.get_host_by_uuid(hostB1.uuid)
    hostA1.pool.eject_host(joined_host)
