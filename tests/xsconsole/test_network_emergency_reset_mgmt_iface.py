import pytest

import time

from lib.common import Defer
from lib.host import Host

from typing import Iterator

CONFIG_PATH = "/etc/xsconsole/activatexmlrpc"
SOCKET_PATH = "/var/xapi/xmlrpcsocket.xsconsole"

# Test requirements:
# - An XCP-ng host with 2 network interfaces:
#   * eth0 for management, configured with DHCP
#   * eth1 to test the renaming, no configuration needed
# - Note that test relies on the DHCP providing the same IP address
#   after an emergency network reset

@pytest.fixture
def activate_xsconsole_xmlrpc(host: Host) -> Iterator[str]:
    target_file = CONFIG_PATH
    host.ssh(f"touch {target_file}")
    assert host.file_exists(target_file), f"The file {target_file} was not found on the remote node."
    yield target_file
    host.ssh(f"unlink {target_file}")


@pytest.mark.parametrize("reset_nics", [False, True], ids=["no_reset", "reset_nics"])
def test_retain_mgmt_iface(
    host: Host,
    reset_nics: bool,
    activate_xsconsole_xmlrpc: str,
    defer: Defer,
):
    from data import HOST_DEFAULT_PASSWORD

    # Besides the management interface (eth0), the target host needs to
    # have another interface (eth1) that will be the one that this tests
    eth1_mac = host.ssh("interface-rename --list | grep -i eth1 | awk '{ print $2 }'")
    host.ssh("screen -dmS xsconsole-session xsconsole")

    host.ssh("ip link set eth1 down")
    eth1_pif_uuid = host.ssh("xe pif-list device=eth1 --minimal")
    if eth1_pif_uuid:
        host.ssh(f"xe pif-forget uuid={eth1_pif_uuid}")
    host.ssh("ip link set eth1 name eth999")
    host.ssh("ip link set eth999 up")

    def cleanup():
        if "eth999" in host.ssh("ip link show"):
            host.ssh("ip link set eth999 down")
            host.ssh("ip link set eth999 name eth1")
            host.ssh("ip link set eth1 up")
        eth999_pif_uuid = host.ssh("xe pif-list device=eth999 --minimal")
        if eth999_pif_uuid:
            host.ssh(f"xe pif-forget uuid={eth999_pif_uuid}")

    defer(cleanup)

    host.ssh(f"interface-rename --update eth999='{eth1_mac}'")
    host.ssh("xe pif-scan host-uuid=$(xe host-list --minimal)")

    host.scp("scripts/xsconsole-emergency-network-reset.py", "/root/xsconsole-emergency-network-reset.py")

    host.ssh(f"sed -i 's/REPLACE-PASSWORD/{HOST_DEFAULT_PASSWORD}/' /root/xsconsole-emergency-network-reset.py")

    host.ssh("chmod 700 /root/xsconsole-emergency-network-reset.py")
    host.ssh(f"python3 /root/xsconsole-emergency-network-reset.py {int(reset_nics)}")

    # wait for host to be powered-up again and XAPI services to start
    host.wait_for_host_down()
    host.wait_for_host_up()
    host.wait_for_ssh_reachable()
    host.wait_for_xapi_enabled()

    new_iface = host.ssh(f"xe pif-list MAC={eth1_mac} params=device --minimal")

    if reset_nics:
        assert new_iface == "eth1", "The management interface has NOT changed from eth999 to eth1"
    else:
        assert new_iface == "eth999", f"The management interface has changed from eth999 to '{new_iface}'"
