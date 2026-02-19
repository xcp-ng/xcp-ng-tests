import pytest

import logging
import re

from lib.host import Host

# Misc tests for Xen host
#
# Requirements:
# - XCP-ng host

# Check for a valid "Latest ChangeSet" trace in 'xl dmesg'
def test_xen_changeset(host: Host):
    changeset = host.ssh('xl dmesg | grep "Latest ChangeSet"')
    regexp = r'.*Latest ChangeSet:\s*(([^,]+),.*)'

    m = re.match(regexp, changeset)
    assert m is not None, "'Latest ChangeSet' should be found in 'xl dmesg'"

    full_changeset, git_sha = m.groups()
    logging.info(f"Xen Latest ChangeSet: {full_changeset}")

    m = re.match(r'[0-9a-fA-F]+', git_sha)
    assert m is not None, "Xen Latest ChangeSet should be set and formatted as a commit hash"
