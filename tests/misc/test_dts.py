import pytest

import logging

from lib.cpu_policy import NO_SUBLEAF, HostCpuPolicy

# Requirements:
# - one XCP-ng host (--host) >= 8.3
# - Intel CPU with DTS support (i.e no nested virtualization)

@pytest.mark.usefixtures("host_at_least_8_3")
class TestDts:
    def test_dts(self, host):
        cpu_policy = HostCpuPolicy(host)

        power_leaf = cpu_policy.policies["Host"].cpuid.get((0x6, NO_SUBLEAF))
        
        if power_leaf is None or power_leaf[0] & 1 == 0:
            pytest.skip("This test require a host with DTS support")
            return

        host.ssh(["xenpm get-intel-temp"])
