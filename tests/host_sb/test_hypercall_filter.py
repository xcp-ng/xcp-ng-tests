import pathlib

import pytest

from lib.host import Host

# Requirements:
# - one XCP-ng host (--host) >= 9.0
# - Host Secureboot enforcement must be enabled

def test_hypercall_filter(host: Host):
    
    """
        Verify the Xen privcmd hypercall filter.
    
        The helper binary performs a collection of safe hypercalls and verifies
        that:
          * allowed read-only hypercalls succeed
          * invalid guest pointers are rejected with -EFAULT
          * forbidden operations are rejected by the filter
          * unknown operations return the expected error
    
        The helper exits with status 0 iff every check passes.
        """
    
    state = host.ssh("mokutil --sb-state", simple_output=True)
    if "SecureBoot enabled" not in state:
      pytest.skip("Secure Boot is disabled")

    local_binary = pathlib.Path(__file__).parent / "data" / "test_hypercall_filter"
    remote_binary = "/tmp/test_hypercall_filter"

    host.scp(str(local_binary), remote_binary)
    host.ssh(f"chmod +x {remote_binary}")
    host.ssh(remote_binary)