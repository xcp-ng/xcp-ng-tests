from __future__ import annotations

import pytest

import logging
import re
from enum import StrEnum

from lib.common import Defer, safe_split
from lib.host import Host

# Requirements:
# From --hosts parameter:
# - host(A1): an XCP-ng host

# no need to be real NTP servers
TEST_NTP_SERVERS = ['127.0.0.1', '127.0.0.2']

RE_CHRONY_SERVERS = re.compile(r"server ([^ ]+)( iburst)?")
RE_XENSOURCE = re.compile(r".* factory-ntp-servers = \[ (.*) \] .*")

class NtpMode(StrEnum):
    DHCP = 'DHCP'
    Custom = 'Custom'
    Factory = 'Factory'
    Disabled = 'Disabled'

class NtpService:
    config: str
    servers: list[str]
    use_dhcp: bool
    service_active: bool

class Chrony(NtpService):
    def __init__(self, host: Host, conf_path: str) -> None:
        # read conf_path and parse it
        self.config = host.ssh(f"cat {conf_path}")
        self.servers = []
        self.use_dhcp = False

        for line in self.config.splitlines():
            m = RE_CHRONY_SERVERS.fullmatch(line)
            if m is not None:
                self.servers.append(m.group(1))
                continue

        # DHCP: check if dhclient script hook is executable
        self.use_dhcp = host.ssh_with_result(
            "test -x /etc/dhcp/dhclient.d/chrony.sh",
        ).returncode == 0

        # check running service
        self.service_active = host.service_started("chronyd.service")

        # workaround: we are starting the service too many time and hit systemd start-limit
        host.ssh("systemctl reset-failed chronyd.service")


def host_ntp_mode(host: Host, value: NtpMode | None = None) -> NtpMode:
    old = host.param_get('ntp-mode')

    if value is not None and value != old:
        logging.info(f"Setting ntp-mode: {old}->{value}")
        host.param_set('ntp-mode', value)

    return NtpMode(old)

def host_ntp_custom_servers(host: Host, values: list[str] | None = None) -> list[str]:
    old = safe_split(host.param_get('ntp-custom-servers'), sep=', ')

    if values is not None and values != old:
        logging.info(f"Setting ntp-custom-servers: {old}->{values}")
        host.param_set('ntp-custom-servers', ','.join(values))

    return old

def host_xapi_factory_ntp_servers(host: Host) -> list[str]:
    xensource_log = set()

    # use journalctl (XCP-ng >= 9.0)
    xensource_log.update(host.ssh(
        "journalctl --boot --unit=xapi --priority=debug --grep=' factory-ntp-servers = '",
        check=False,
    ).splitlines())

    # use /var/log/xensource.log (XCP-ng < 9.0)
    xensource_log.update(host.ssh(
        "grep -F ' factory-ntp-servers = ' /var/log/xensource.log",
        check=False,
    ).splitlines())

    # extract values from xensource_log
    servers = set()
    for line in xensource_log:
        m = RE_XENSOURCE.match(line)
        if m is not None:
            servers.update(m.group(1).split("; "))

    if len(servers) == 0:
        # no factory default found in logs, use hardcoded list
        return [
            "0.centos.pool.ntp.org",
            "1.centos.pool.ntp.org",
            "2.centos.pool.ntp.org",
            "3.centos.pool.ntp.org",
        ]

    return list(servers)

def host_ntpconf(host: Host) -> NtpService:
    # support only chrony
    return Chrony(host, "/etc/chrony.conf")

@pytest.mark.no_vm
class TestNtpMode:

    def test_idempotence(self, host: Host, defer: Defer) -> None:
        """
        Test that mode -> newmode -> mode is idempotent
        """
        initial_mode = host_ntp_mode(host)
        defer(lambda: host_ntp_mode(host, initial_mode))

        previous_servers = host_ntp_custom_servers(host, TEST_NTP_SERVERS)
        defer(lambda: host_ntp_custom_servers(host, previous_servers))

        for mode1 in NtpMode:
            for mode2 in NtpMode:
                if mode1 == mode2:
                    continue

                logging.info(f"Checking {mode1} -> {mode2} -> {mode1}")
                host_ntp_mode(host, mode1)
                configA = host_ntpconf(host).config
                host_ntp_mode(host, mode2)
                host_ntp_mode(host, mode1)
                configB = host_ntpconf(host).config

                if configA != configB:
                    pytest.fail(f"idempotence failed in {mode1} -> {mode2} -> {mode1}")

    def test_factory(self, host: Host, defer: Defer) -> None:
        """
        Test for ntp-mode=Factory
        """
        previous = host_ntp_mode(host, NtpMode.Factory)
        defer(lambda: host_ntp_mode(host, previous))

        factory_defaults = host_xapi_factory_ntp_servers(host)
        logging.debug(f"Default factory servers: {factory_defaults}")

        ntpconf = host_ntpconf(host)

        if not ntpconf.service_active:
            pytest.fail("ntp service is not running")

        if ntpconf.use_dhcp:
            pytest.fail("ntp configuration has DHCP enabled whereas in Factory mode")

        if ntpconf.servers == []:
            pytest.fail("ntp configuration has no servers configured")

        for server in ntpconf.servers:
            if server not in factory_defaults:
                pytest.fail("configured ntp servers aren't the factory ones")

    def test_dhcp(self, host: Host, defer: Defer) -> None:
        """
        Test for ntp-mode=DHCP
        """
        previous = host_ntp_mode(host, NtpMode.DHCP)
        defer(lambda: host_ntp_mode(host, previous))

        ntpconf = host_ntpconf(host)

        if not ntpconf.service_active:
            pytest.fail("ntp service is not running")

        if not ntpconf.use_dhcp and host.management_pif().ip_configuration_mode() == 'DHCP':
            pytest.fail("ntp configuration has no DHCP enabled ntp servers")

        if ntpconf.servers != []:
            pytest.fail("ntp configuration has static servers configured")

    def test_custom(self, host: Host, defer: Defer) -> None:
        """
        Test for ntp-mode=Custom
        """
        previous_servers = host_ntp_custom_servers(host, TEST_NTP_SERVERS)
        defer(lambda: host_ntp_custom_servers(host, previous_servers))

        previous = host_ntp_mode(host, NtpMode.Custom)
        defer(lambda: host_ntp_mode(host, previous))

        ntpconf = host_ntpconf(host)

        if not ntpconf.service_active:
            pytest.fail("ntp service is not running")

        if ntpconf.use_dhcp:
            pytest.fail("ntp configuration has DHCP enabled whereas in Custom mode")

        custom_servers = set(host_ntp_custom_servers(host))
        servers = set(ntpconf.servers)
        if servers != custom_servers:
            pytest.fail("ntp servers aren't the custom ones")

    def test_disabled(self, host: Host, defer: Defer) -> None:
        """
        Test for ntp-mode=Disabled
        """
        previous = host_ntp_mode(host, NtpMode.Disabled)
        defer(lambda: host_ntp_mode(host, previous))

        ntpconf = host_ntpconf(host)

        if ntpconf.service_active:
            pytest.fail("ntp service is not stopped")
