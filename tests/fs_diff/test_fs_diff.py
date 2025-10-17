import pytest

import os

from lib.commands import local_cmd

# Requirements:
# - 2 XCP-ng host of same version

pytest.fixture(scope='module')
def test_fs_diff(hosts):
    assert len(hosts) == 2, "This test requires exactly 2 hosts"

    assert (hosts[0].xcp_version == hosts[1].xcp_version), "Host versions must be the same"

    fsdiff = os.path.realpath(f"{os.path.dirname(__file__)}/../../scripts/xcpng-fs-diff.py")

    res = local_cmd([fsdiff, "--reference-host", f"{hosts[0]}",
                             "--test-host", f"{hosts[1]}",
                             "--json-output"])

    if res.returncode != 0:
        print(res.stdout)

    assert res.returncode == 0
