from __future__ import annotations

import pytest

import logging

from .utils import KernelChecks, netns_cleanup

from typing import Any, Generator

# Requirements:
# From --hosts parameter:
# - host(A1): any XCP-ng host with python3 in dom0.
#
# TCP connection churn in dom0 network namespaces: many short connections (plain, Fast Open, client and server
# RST, half-close) over loopback and over a veth pair, IPv4 and IPv6, with each SYN cookies mode. This exercises
# the listener/established socket lookups, request sockets, TIME_WAIT and SYN cookies, which several recent
# backports touched (NoisyTCP CVE-2026-99288, sk_rx_dst CVE-2021-47103, inet_opt CVE-2026-98432).

NS_A, NS_B = 'xcpngtest-tcpa', 'xcpngtest-tcpb'
ADDRS = {4: ('10.198.0.1', '10.198.0.2'), 6: ('fd99:198::1', 'fd99:198::2')}
DURATION = 8
CLIENTS = 32
# Connections may fail occasionally under such a load (e.g. the first Fast Open attempts), but rarely
MAX_ERROR_RATIO = 0.01
CONNTRACK_MAX = '/proc/sys/net/netfilter/nf_conntrack_max'

@pytest.fixture(scope='module')
def tcp_netns(kernel_checks: KernelChecks) -> Generator[KernelChecks, None, None]:
    host = kernel_checks.host
    # The conntrack table is shared by all namespaces in 4.19, and dom0's default size fills up quickly
    conntrack_max = host.ssh(f'cat {CONNTRACK_MAX} 2>/dev/null || true')
    if conntrack_max:
        host.ssh(f'echo 1048576 > {CONNTRACK_MAX}')
    netns_cleanup(host, NS_A, NS_B)
    cmds = [
        f'ip netns add {NS_A}', f'ip netns add {NS_B}',
        'ip link add xcpngt-ta type veth peer name xcpngt-tb',
        f'ip link set xcpngt-ta netns {NS_A}', f'ip link set xcpngt-tb netns {NS_B}',
        f'ip -n {NS_A} addr add {ADDRS[4][0]}/24 dev xcpngt-ta',
        f'ip -n {NS_B} addr add {ADDRS[4][1]}/24 dev xcpngt-tb',
        f'ip -n {NS_A} addr add {ADDRS[6][0]}/64 dev xcpngt-ta nodad',
        f'ip -n {NS_B} addr add {ADDRS[6][1]}/64 dev xcpngt-tb nodad',
        f'ip -n {NS_A} link set xcpngt-ta up', f'ip -n {NS_B} link set xcpngt-tb up',
    ]
    for ns in (NS_A, NS_B):
        cmds += [f'ip -n {ns} link set lo up',
                 f"ip netns exec {ns} sysctl -qw net.ipv4.tcp_fastopen=3 net.ipv4.tcp_tw_reuse=1 "
                 f"net.ipv4.ip_local_port_range='10000 65000'"]
    try:
        host.ssh(' && '.join(cmds))
        yield kernel_checks
    finally:
        netns_cleanup(host, NS_A, NS_B)
        if conntrack_max:
            host.ssh(f'echo {conntrack_max} > {CONNTRACK_MAX}')

def check_stats(what: str, stats: dict[str, Any]) -> None:
    errors = sum(stats['errors'].values())
    logging.info(f"{what}: {stats['ok']} connections, {errors} errors {stats['errors']}")
    assert stats['ok'] > 100, f"{what}: only {stats['ok']} successful connections"
    assert errors <= MAX_ERROR_RATIO * stats['ok'], f"{what}: too many errors {stats['errors']}"

@pytest.mark.parametrize('syncookies', [0, 1, 2])
def test_tcp_churn(tcp_netns: KernelChecks, syncookies: int) -> None:
    for ns in (NS_A, NS_B):
        tcp_netns.host.ssh(f'ip netns exec {ns} sysctl -qw net.ipv4.tcp_syncookies={syncookies}')

    for addr in ('127.0.0.1', '::1'):
        [stats] = tcp_netns.run('tcp-churn', addr, 6000, DURATION, CLIENTS, netns=NS_A)
        check_stats(f"loopback {addr}", stats)

    for fam in (4, 6):
        server = ADDRS[fam][1]
        tcp_netns.start('server', 'tcp-server', server, 7000, DURATION + 3, netns=NS_B)
        [stats] = tcp_netns.run('tcp-clients', server, 7000, DURATION, CLIENTS, netns=NS_A)
        tcp_netns.wait('server')
        check_stats(f"veth IPv{fam}", stats)
