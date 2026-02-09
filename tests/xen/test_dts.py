import pytest

import logging

from lib.cpu_policy import NO_SUBLEAF, HostCpuPolicy

# Requirements:
# - one XCP-ng host (--host) >= 8.3
# - Intel CPU with DTS support (i.e no nested virtualization)

@pytest.mark.usefixtures("host_at_least_8_3")
class TestDts:
    def test_dts(self, host):
        """
        We can't meaningfully check if DTS/PTS hardware feature works neither
        if Xen returns correct values. The goal here is to make sure that DTS
        bit exposed implies that `xenpm get-core-temp` doesn't fail.

        The use of Host policy requires [1] backport, otherwise, Host policy
        doesn't match the hardware CPUID. IOW, the test assumes that this backport
        exist, or at least, if it's missing, it's only because the DTS feature hasn't
        been implemented (yet) in this XCP-ng build.

        [1] f6894fdf ("x86/cpu-policy: move invocation of recalculate_misc()")
        https://gitlab.com/xen-project/xen/-/commit/f6894fdfa83359d0686a41aee5c2a8ba7b2878b4
        """
        cpu_policy = HostCpuPolicy(host)

        power_leaf = cpu_policy.policies["Host"].cpuid.get((0x6, NO_SUBLEAF))

        if power_leaf is None or power_leaf.eax & 1 == 0:
            pytest.skip("This test require a host with DTS support")

        host.ssh("xenpm get-core-temp")
