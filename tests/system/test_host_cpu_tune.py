import pytest

import logging
import re

from lib.common import Defer
from lib.host import Host

# Requirements:
# - an XCP-ng host (--hosts) with at least 2 pCPUs

CPU_TUNE = "/opt/xensource/bin/host-cpu-tune"

def dom0_max_vcpus(host: Host) -> int | None:
    match = re.search(r"dom0_max_vcpus=1-(\d+)", host.ssh("xl info xen_commandline"))
    return int(match.group(1)) if match else None

def is_xpin(host: Host) -> bool:
    # host-cpu-tune enables exclusive pinning by adding a bare `dom0_vcpus_pin` to the xen command line
    return "dom0_vcpus_pin" in host.ssh("xl info xen_commandline")

@pytest.mark.reboot # Need to reboot to check Xen command line changes.
def test_host_cpu_tune_changes_xen_cmdline(host: Host, defer: Defer) -> None:
    nr_pcpus = int(host.ssh("xl info nr_cpus"))
    original_cmdline = host.ssh("xl info xen_commandline").strip()
    original_n = dom0_max_vcpus(host)
    original_pinning = "xpin" if is_xpin(host) else "nopin"
    current = original_n or nr_pcpus

    assert nr_pcpus >= 2, f"Not enough pCPUs, need 2, got {nr_pcpus}."

    # pick a valid dom0 vCPU count different from the current one
    n = (current - 1) if current > 1 else 2

    # restore the original configuration and reboot, even if the test fails partway through
    restore_n = original_n if original_n is not None else nr_pcpus
    defer(lambda: (
        host.ssh(f"{CPU_TUNE} set {restore_n} {original_pinning}"),
        host.reboot(verify=True),
    ))

    logging.info("Setting dom0 vCPU count to %d with host-cpu-tune", n)
    output = host.ssh(f"{CPU_TUNE} set {n} nopin")
    assert "Configuration successfully applied" in output, output

    # Reboot the host, as this is the only way to find out if Xen command line
    # changed are been applied.
    host.reboot(verify=True)

    new_cmdline = host.ssh("xl info xen_commandline").strip()
    assert f"dom0_max_vcpus=1-{n}" in new_cmdline, (
        f"dom0_max_vcpus=1-{n} not found in xen command line after reboot:\n{new_cmdline}"
    )
    assert new_cmdline != original_cmdline, "xen command line was not changed by host-cpu-tune"
    assert dom0_max_vcpus(host) == n
    # dom0 actually got n vCPUs after the reboot
    vcpu_count = len(host.ssh("xl vcpu-list 0").splitlines()) - 1
    assert vcpu_count == n, f"dom0 has {vcpu_count} vCPUs after reboot, expected {n}"
