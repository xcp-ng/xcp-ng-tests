import pytest

# This test is designed to verify the accessibility of the XOA deployment script
#
# Requirements:
# - an XCP-ng host with xcp-ng-release >= 8.3.0.29

def test_xoa_deploy_with_curl(host):
    """
    Verifies that the URL https://xoa.io/deploy responds correctly via curl.
    """
    result = host.ssh("curl -fsSL https://xoa.io/deploy")
    assert result.startswith("#!/bin/bash"), "The XOA deployment theoretically impossible. (Issue with curl?)"

def test_xoa_deploy_with_wget(host):
    """
    Verifies that the URL https://xoa.io/deploy responds correctly via wget.
    """
    result = host.ssh("wget -qO- https://xoa.io/deploy")
    assert result.startswith("#!/bin/bash"), "The XOA deployment theoretically impossible. (Issue with wget?)"
