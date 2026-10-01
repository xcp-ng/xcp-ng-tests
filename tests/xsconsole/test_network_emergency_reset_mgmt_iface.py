import pytest
import time

CONFIG_PATH = "/etc/xsconsole/activatexmlrpc"
SOCKET_PATH = "/var/xapi/xmlrpcsocket.xsconsole"


class TestEmergencyNetworkReset:

    def create_and_test_config_file_exists(self, host):
        target_file = CONFIG_PATH
        host.ssh(f"touch {target_file}")
        
        assert host.file_exists(target_file), f"The file {target_file} was not found on the remote node."

    def test_retain_mgmt_iface(self, host, host_password, reset_nics):
        # Besides the management interface (eth0), the target host needs to
        # have another interface (eth1) that will be the one that this tests
        eth1_mac = host.ssh("interface-rename --list | grep -i eth1 | awk '{ print $2 }'")

        host.ssh("screen -dmS xsconsole-session xsconsole")

        host.ssh("ip link set eth1 down")
        host.ssh("xe pif-forget uuid=$(xe pif-list device=eth1 --minimal)")
        host.ssh("ip link set eth1 name eth999")
        host.ssh("ip link set eth999 up")

        host.ssh(f"interface-rename --update eth999='{eth1_mac}'")
        host.ssh("xe pif-scan host-uuid=$(xe host-list --minimal)")

        host.scp("scripts/xsconsole-emergency-network-reset.py", "/root/xsconsole-emergency-network-reset.py")

        host.ssh(f"echo {host_password} > /root/test-password")
        host.ssh(f"sed -i 's/REPLACE-PASSWORD/{host_password}/' /root/xsconsole-emergency-network-reset.py")

        host.ssh("chmod 700 /root/xsconsole-emergency-network-reset.py")
        host.ssh(f"python3 /root/xsconsole-emergency-network-reset.py {reset_nics}")

        # wait for host to be powered-up again and XAPI services to start
        time.sleep(150)

        new_iface = host.ssh(f"xe pif-list MAC={eth1_mac} params=device --minimal")

        if reset_nics == 1:
            if new_iface == "eth999":
                pytest.fail(f"The management interface has NOT changed from eth999 to eth1")
        else:
            if new_iface != "eth999":
                pytest.fail(f"The management interface has changed from eth999 to '{new_iface}'")
