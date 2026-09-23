from lib.common import Defer
from lib.host import Host

# Regression test for sshd silently ignoring /etc/ssh/sshd_config.d/*.conf
# drop-in files (missing "Include" directive in the packaged sshd_config).
#
# A drop-in file is created, setting a directive (a pre-auth Banner), that
# isn't set anywhere else. `sshd -T` dumps the configuration as sshd itself
# would apply it, so finding the directive there proves the drop-in directory
# is actually included.
#
# Requirements:
# - an XCP-ng host (--hosts) >= 8.2

SSHD_CONFIG_D = "/etc/ssh/sshd_config.d"

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
