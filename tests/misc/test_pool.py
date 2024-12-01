from lib import host

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.2.
# - hostB1: Master of a second pool.

def test_pool_join(hostA1, hostB1):
    hostB1.join_pool(hostA1.pool)
