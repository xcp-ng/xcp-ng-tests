import pytest

import logging

from lib.cpu_policy import NO_SUBLEAF, HostCpuPolicy
from lib.host import Host

# Xen CPU Policy gathering test
#
# # Requirements:
# - XCP-ng host

class TestCpuPolicy:
    def test_cpu_policy_collection(self, host: Host) -> None:
        """
        This test simply collects the CPU policy (CPUIDs and MSRs) from the host.
        We only check that we collected the information successfully, without errors.
        No extra check is made on the accuracy or completeness of the information.
        A side effect is the logging of the collected information in the test output.
        """
        cpu_policy = HostCpuPolicy(host)

        for name, policy in cpu_policy.policies.items():
            for ((leaf, subleaf), regs) in policy.cpuid.items():
                eax, ebx, ecx, edx = regs.eax, regs.ebx, regs.ecx, regs.edx
                logging.info(f"CPUID[{name}]: {leaf:08x}:{subleaf:08x} -> {eax:08x}:{ebx:08x}:{ecx:08x}:{edx:08x}")

            for (msr, val) in policy.msr.items():
                logging.info(f"MSR  [{name}]: {msr:08x} -> {val:016x}")
