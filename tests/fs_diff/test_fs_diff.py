import pytest

import os
import subprocess

from lib.host import Host

# Requirements:
# - 2 XCP-ng host of same version

pytest.fixture(scope='module')
def test_fs_diff(hosts: list[Host]) -> None:
    assert len(hosts) == 2, "This test requires exactly 2 hosts"

    assert (hosts[0].xcp_version == hosts[1].xcp_version), "Host versions must be the same"

    fsdiff = os.path.realpath(f"{os.path.dirname(__file__)}/../../scripts/xcpng-fs-diff.py")

    process = subprocess.Popen(
        [fsdiff, "--reference-host", f"{hosts[0]}", "--test-host", f"{hosts[1]}", "--json-output"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, _ = process.communicate()

    if process.returncode != 0:
        print(stdout.decode())

    assert process.returncode == 0
