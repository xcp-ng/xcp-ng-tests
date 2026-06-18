from __future__ import annotations

import logging
from contextlib import contextmanager

from lib.host import Host
from tests.ha.net import host_is_reachable

from typing import Generator

# NFS + portmapper (NFSv3)
NFS_BLOCK_PORTS = (2049, 111)


def output_drop_specs(destination: str, *, ports: tuple[int, ...]) -> list[str]:
    specs = []
    for port in ports:
        for proto in ('tcp', 'udp'):
            specs.append(f'OUTPUT -p {proto} -d {destination} --dport {port} -j DROP')
    return specs


def _insert(host: Host, rule_spec: str) -> None:
    logging.info('iptables -I %s on %s', rule_spec, host)
    host.ssh(f'iptables -I {rule_spec}')


def _delete(host: Host, rule_spec: str) -> None:
    if not host_is_reachable(host):
        return
    logging.info('iptables -D %s on %s', rule_spec, host)
    host.ssh(f'iptables -D {rule_spec}', check=False)


def install_persistent_rules(host: Host, rule_specs: list[str]) -> None:
    logging.info('Install persistent NFS block on %s', host)
    for spec in rule_specs:
        _insert(host, spec)
    host.ssh('service iptables save')


def remove_persistent_rules(host: Host, rule_specs: list[str]) -> bool:
    """Remove DROP rules and save iptables. Returns False if host is unreachable."""
    if not host_is_reachable(host):
        return False
    logging.info('Remove persistent NFS block on %s', host)
    for spec in rule_specs:
        _delete(host, spec)
    host.ssh('service iptables save', check=False)
    return True


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
            _delete(host, rule_spec)
