"""iptables DROP rules for HA network / NFS partition tests."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from lib.host import Host
from tests.ha.net import host_is_reachable

# NFS + portmapper (NFSv3)
NFS_BLOCK_PORTS = (2049, 111)


def output_drop_specs(destination: str, *, ports: tuple[int, ...]) -> list[str]:
    specs: list[str] = []
    for port in ports:
        for proto in ('tcp', 'udp'):
            specs.append(f'OUTPUT -p {proto} -d {destination} --dport {port} -j DROP')
    return specs


def peer_cut_specs(peer: Host) -> list[str]:
    peer_ip = str(peer.hostname_or_ip)
    return [
        f'OUTPUT -d {peer_ip} -j DROP',
        f'INPUT -s {peer_ip} -j DROP',
    ]


def _insert(host: Host, rule_spec: str) -> None:
    logging.info('iptables -I %s on %s', rule_spec, host)
    host.ssh(f'iptables -I {rule_spec}')


def _delete(host: Host, rule_spec: str) -> None:
    if not host_is_reachable(host):
        logging.info('Skip iptables -D on unreachable %s: %s', host, rule_spec)
        return
    logging.info('iptables -D %s on %s', rule_spec, host)
    result = host.ssh(f'iptables -D {rule_spec}', check=False, simple_output=False)
    if result.returncode != 0:
        logging.info(
            'iptables -D on %s returned %s: %s',
            host,
            result.returncode,
            (result.stdout or '').strip(),
        )


def delete_all(host: Host, rule_specs: list[str]) -> None:
    for spec in rule_specs:
        try:
            _delete(host, spec)
        except Exception:
            logging.warning(
                'Could not remove iptables rule on %s: %s',
                host,
                spec,
                exc_info=True,
            )


@contextmanager
def iptables_rules(host_rules: list[tuple[Host, str]]) -> Generator[None, None, None]:
    applied: list[tuple[Host, str]] = []
    try:
        for host, rule_spec in host_rules:
            _insert(host, rule_spec)
            applied.append((host, rule_spec))
        yield
    finally:
        for host, rule_spec in reversed(applied):
            try:
                _delete(host, rule_spec)
            except Exception:
                logging.warning(
                    'Could not remove iptables rule on %s: %s',
                    host,
                    rule_spec,
                    exc_info=True,
                )
