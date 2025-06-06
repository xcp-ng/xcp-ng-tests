import pytest
import subprocess
import hashlib

from lib import commands

# This test is designed to verify the accessibility of the XOA deployment script
#
# Requirements:
# - an XCP-ng host with xcp-ng-release >= 8.3.0.29

@pytest.mark.parametrize("command_id", ["curl", "wget"])
@pytest.mark.parametrize("url_id", [
    "xoa",
    "xcpng",
    "vates"
])
def test_access_links(host, command_id, url_id):
    """
    Verifies that the specified URL responds correctly via the specified command
    and compares the checksum of the downloaded content between local and remote.
    """
    command = {"curl": "curl -fsSL",
               "wget": "wget -qO-"}[command_id]
    url = {
        "xoa": "https://xoa.io/deploy",
        "xcpng": "https://updates.xcp-ng.org/trace",
        "vates": "https://repo.vates.tech/README.txt"
    }[url_id]
    COMMAND = f"{command} '{url}'"

    # Download from remote host
    remote_result = host.ssh(COMMAND)

    # Verify the download worked by comparing with local download
    # This ensures the content is accessible and identical from both locations
    local_result = commands.local_cmd(COMMAND)

    assert local_result.returncode == 0, (
        f"Failed to fetch URL locally: {local_result.stderr}"
    )

    # Extract checksums
    local_checksum = hashlib.sha256(local_result.stdout.split()[0].encode('utf-8')).hexdigest()
    remote_checksum = hashlib.sha256(remote_result.split()[0].encode('utf-8')).hexdigest()

    assert local_checksum == remote_checksum, (
        f"Checksum mismatch: local ({local_checksum}) != remote ({remote_checksum})"
    )
