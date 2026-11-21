from __future__ import annotations

import pytest

import re

from lib.host import Host

from .utils import KernelChecks, netns_cleanup

from typing import Generator

# Requirements:
# From --hosts parameter:
# - host(A1): any XCP-ng host with python3 in dom0.
#
# IPsec (ESP, transport mode) between two network namespaces of dom0 linked by a veth pair, with the AEAD
# constructions the kernel builds from the SAs: authenc(hmac(...),cbc(aes)), rfc4106(gcm(aes)), rfc4543(gcm(aes)),
# and authencesn(...) for SAs using extended sequence numbers.

NS_A, NS_B = 'xcpngtest-ipa', 'xcpngtest-ipb'
ADDRS = {4: ('10.199.0.1', '10.199.0.2'), 6: ('fd99:199::1', 'fd99:199::2')}
PING_SIZES = [0, 1, 56, 1000, 1400, 1472, 1500, 2000, 9000, 30000, 65000]
STREAM_SIZE = 16 * 1024 * 1024
PORT = 5555

K16 = '0x000102030405060708090a0b0c0d0e0f'
K20 = K16 + '10111213'
K32 = K16 + '101112131415161718191a1b1c1d1e1f'
K36 = K32 + '20212223'

SUITES = {
    'hmac-sha256-cbc-aes': f"auth-trunc 'hmac(sha256)' {K32} 128 enc 'cbc(aes)' {K16}",
    'hmac-sha1-cbc-aes': f"auth 'hmac(sha1)' {K20} enc 'cbc(aes)' {K16}",
    'rfc4106-gcm-aes128': f"aead 'rfc4106(gcm(aes))' {K20} 128",
    'rfc4106-gcm-aes256': f"aead 'rfc4106(gcm(aes))' {K36} 128",
    'rfc4543-gmac-aes128': f"aead 'rfc4543(gcm(aes))' {K20} 128",
}
ESN = 'replay-window 128 flag esn'

@pytest.fixture(scope='module')
def netns_pair(kernel_checks: KernelChecks) -> Generator[KernelChecks, None, None]:
    host = kernel_checks.host
    netns_cleanup(host, NS_A, NS_B)
    host.ssh(' && '.join([
        f'ip netns add {NS_A}', f'ip netns add {NS_B}',
        'ip link add xcpngt-va type veth peer name xcpngt-vb',
        f'ip link set xcpngt-va netns {NS_A}', f'ip link set xcpngt-vb netns {NS_B}',
        f'ip -n {NS_A} addr add {ADDRS[4][0]}/24 dev xcpngt-va',
        f'ip -n {NS_B} addr add {ADDRS[4][1]}/24 dev xcpngt-vb',
        f'ip -n {NS_A} addr add {ADDRS[6][0]}/64 dev xcpngt-va nodad',
        f'ip -n {NS_B} addr add {ADDRS[6][1]}/64 dev xcpngt-vb nodad',
        f'ip -n {NS_A} link set lo up', f'ip -n {NS_B} link set lo up',
        f'ip -n {NS_A} link set xcpngt-va up', f'ip -n {NS_B} link set xcpngt-vb up',
    ]))
    yield kernel_checks
    netns_cleanup(host, NS_A, NS_B)

def add_sas(host: Host, fam: int, algs: str) -> None:
    a, b = ADDRS[fam]
    cmds = []
    for ns in (NS_A, NS_B):
        cmds += [f'ip -n {ns} xfrm state flush', f'ip -n {ns} xfrm policy flush']
        for src, dst, spi in ((a, b, '0x1001'), (b, a, '0x1002')):
            cmds.append(f'ip -n {ns} xfrm state add src {src} dst {dst} proto esp spi {spi} reqid 1 '
                        f'mode transport {algs}')
    for ns, local, remote in ((NS_A, a, b), (NS_B, b, a)):
        for src, dst, direction in ((local, remote, 'out'), (remote, local, 'in')):
            cmds.append(f'ip -n {ns} xfrm policy add src {src} dst {dst} dir {direction} '
                        f'tmpl src {src} dst {dst} proto esp reqid 1 mode transport')
    host.ssh(' && '.join(cmds))

def esp_stats(host: Host, ns: str) -> tuple[int, list[int]]:
    """Bytes processed by the SAs of the namespace, and their failure counters."""
    out = host.ssh(f'ip -n {ns} -s xfrm state')
    nbytes = sum(int(m) for m in re.findall(r'lifetime current:\s*\n\s*(\d+)\(bytes\)', out))
    failed = [int(m) for m in re.findall(r'\bfailed (\d+)', out)]
    return nbytes, failed

def check_traffic(checks: KernelChecks, fam: int) -> None:
    host = checks.host
    b = ADDRS[fam][1]
    ping = 'ping' if fam == 4 else 'ping6'

    lost = [size for size in PING_SIZES
            if host.ssh_with_result(f'ip netns exec {NS_A} {ping} -q -c 2 -i 0.2 -W 2 -s {size} {b}').returncode]
    assert not lost, f"Pings lost through ESP, for payload sizes {lost}"

    checks.start('recv', 'stream-recv', b, PORT, 60, netns=NS_B)
    [sent] = checks.run('stream-send', b, PORT, STREAM_SIZE, netns=NS_A)
    [received] = checks.wait('recv')
    assert received == sent, "Data corrupted through ESP"

    for ns in (NS_A, NS_B):
        nbytes, failed = esp_stats(host, ns)
        assert nbytes >= STREAM_SIZE, f"{ns}: only {nbytes} bytes went through the SAs, is ESP really used?"
        assert not any(failed), f"{ns}: SA failures {failed}"

@pytest.mark.parametrize('fam', [4, 6])
@pytest.mark.parametrize('suite', SUITES.keys())
def test_esp(netns_pair: KernelChecks, suite: str, fam: int) -> None:
    add_sas(netns_pair.host, fam, SUITES[suite])
    check_traffic(netns_pair, fam)

def test_esp_esn(netns_pair: KernelChecks) -> None:
    """Extended sequence numbers need authencesn: either it works, or the SA is cleanly refused."""
    host = netns_pair.host
    esn_available = host.ssh_with_result('modprobe -n authencesn').returncode == 0
    if not esn_available:
        res = host.ssh_with_result(f'ip -n {NS_A} xfrm state flush && ip -n {NS_A} xfrm state add '
                                   f'src {ADDRS[4][0]} dst {ADDRS[4][1]} proto esp spi 0x2001 reqid 2 '
                                   f'mode transport {ESN} {SUITES["hmac-sha256-cbc-aes"]}')
        assert res.returncode != 0, "ESN SA accepted although authencesn isn't available"
        pytest.skip("authencesn is not available: ESN SAs are refused, as expected")
    for fam in (4, 6):
        add_sas(host, fam, f'{ESN} {SUITES["hmac-sha256-cbc-aes"]}')
        check_traffic(netns_pair, fam)
