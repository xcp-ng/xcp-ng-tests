import pytest
import logging

# This test is designed to verify the accessibility of the XOA deployment script
#
# Requirements:
# - XCP-ng 

def test_xoa_deploy(host):
    """
    Verifies that the URL https://xoa.io/deploy responds correctly via curl.
    """
    result = host.ssh("curl -fsSL https://xoa.io/deploy")
    assert result.startswith("#!/bin/bash"), "The deployment script does not start with a bash shebang, making the XOA deployment theoretically impossible. (Issue with curl?)"

    result = host.ssh("wget -qO- https://xoa.io/deploy")
    assert result.startswith("#!/bin/bash"), "The deployment script does not start with a bash shebang, making the XOA deployment theoretically impossible. (Issue with wget?)"
