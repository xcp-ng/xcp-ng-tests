import random

from lib.common import Defer
from lib.host import Host

# Regression test for the ssh client and sshd silently ignoring drop-in
# configuration files (missing "Include" directive in the packaged config).
# Covers both /etc/ssh/ssh_config.d/*.conf (client) and
# /etc/ssh/sshd_config.d/*.conf (server).
#
# Each test creates a drop-in with a directive not set elsewhere, then uses
# `ssh -G` or `sshd -T` to verify that the corresponding directory is included.
#
# Requirements:
# - an XCP-ng host (--hosts) >= 8.2
# - the ssh_config.d test additionally requires the OpenSSH security update:
#   the Include directive isn't in the previous package's ssh_config at all

SSH_CONFIG_D = "/etc/ssh/ssh_config.d"
SSHD_CONFIG_D = "/etc/ssh/sshd_config.d"

def test_ssh_config_d_is_included(host: Host, defer: Defer) -> None:
    marker = str(random.randint(10000, 99999))
    dropin = f"{SSH_CONFIG_D}/99-xcp-ng-tests-include-check.conf"

    host.ssh(f"echo 'ConnectTimeout {marker}' > {dropin}")
    defer(lambda: host.ssh(f"rm -f {dropin}"))

    effective_config = host.ssh("ssh -G localhost")
    assert f"connecttimeout {marker}" in effective_config, (
        f"drop-in {dropin} was not picked up by ssh -G: "
        f"{SSH_CONFIG_D} seems to be ignored"
    )

def test_sshd_config_d_is_included(host: Host, defer: Defer) -> None:
    marker = host.ssh('mktemp')
    dropin = f"{SSHD_CONFIG_D}/99-xcp-ng-tests-include-check.conf"

    host.ssh(f"echo 'Banner {marker}' > {dropin}")
    defer(lambda: host.ssh(f"rm -f {dropin}"))

    effective_config = host.ssh("/usr/sbin/sshd -T")
    assert f"banner {marker}" in effective_config, (
        f"drop-in {dropin} was not picked up by sshd -T: "
        f"{SSHD_CONFIG_D} seems to be ignored"
    )
