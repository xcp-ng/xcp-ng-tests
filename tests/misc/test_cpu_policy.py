import pytest

import logging

from lib.cpu_policy import NO_SUBLEAF, HostCpuPolicy

class TestCpuPolicy:
    def test_cpu_policy(self, host):
        cpu_policy = HostCpuPolicy(host)

        for name, policy in cpu_policy.policies.items():
            for ((leaf, subleaf), (eax, ebx, ecx, edx)) in policy.cpuid.items():
                logging.info(f"CPUID[{name}]: {leaf:08x}:{subleaf:08x} -> {eax:08x}:{ebx:08x}:{ecx:08x}:{edx:08x}")

            for (msr, val) in policy.msr.items():
                logging.info(f"MSR  [{name}]: {msr:08x} -> {val:08x}")
