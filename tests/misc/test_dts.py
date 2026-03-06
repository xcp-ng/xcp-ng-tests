import pytest

import logging

from lib.cpu_policy import NO_SUBLEAF, get_cpu_policy

# Requirements:
# - one XCP-ng host (--host) >= 8.3
# - Intel CPU with DTS support (i.e no nested virtualization)

@pytest.mark.usefixtures("host_at_least_8_3")
class TestDts:
    def test_dts(self, host):
        policy = get_cpu_policy(host)

        power_leaf = policy["Host"]["cpuid"][(0x6, NO_SUBLEAF)]

        if power_leaf["eax"] & 1 != 0:
            pytest.skip("This test require a host with DTS support")
            return

        ret = host.ssh(["xenpm", "get-intel-temp"])

        assert not ("No data" in ret)
