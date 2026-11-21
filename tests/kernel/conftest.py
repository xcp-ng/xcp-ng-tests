from __future__ import annotations

import pytest

import logging
import re
import uuid

from lib.host import Host

from .utils import SCRIPT, KernelChecks

from typing import Generator

# Kernel log messages that must never appear while the tests run
KERNEL_ERRORS_RE = (r'Oops|BUG:|WARNING:|Call Trace|general protection|soft lockup|hung_task|blocked for more than'
                    r'|refcount_t|use-after-free|stack-protector|Kernel panic')

@pytest.fixture(scope='package')
def kernel_checks(host: Host) -> Generator[KernelChecks, None, None]:
    if not host.binary_exists('python3'):
        pytest.skip(f"python3 is required in dom0 of {host}")
    workdir = host.ssh('mktemp -d /root/kernel-checks.XXXXXX')
    host.scp(SCRIPT, f'{workdir}/kernel_checks.py')
    yield KernelChecks(host, workdir)
    host.ssh(f'rm -rf {workdir}')

@pytest.fixture(autouse=True)
def no_kernel_errors(host: Host, request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """Fail the test if the kernel logged an oops, BUG, WARNING, ... while it was running."""
    marker = f'xcp-ng-tests: {request.node.name} {uuid.uuid4()}'
    host.ssh(f"echo '{marker}' > /dev/kmsg")
    yield
    log = host.ssh('dmesg')
    start = log.rfind(marker)
    if start < 0:
        logging.warning(f"[{host}] test marker rotated out of the kernel ring buffer, checking all of it")
        start = 0
    errors = [line for line in log[start:].splitlines() if re.search(KERNEL_ERRORS_RE, line)]
    assert not errors, "Kernel errors logged during the test:\n" + '\n'.join(errors[:20])
