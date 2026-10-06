from __future__ import annotations

import logging

from lib.host import Host

# NFS + portmapper (NFSv3)
NFS_BLOCK_PORTS = (2049, 111)

IPTABLES_CONF = '/etc/sysconfig/iptables'
IPTABLES_BACKUP = f'{IPTABLES_CONF}.xcp-ng-tests-ha'


def nfs_drop_specs(server: str) -> list[str]:
    return [
        f'OUTPUT -p {proto} -d {server} --dport {port} -j DROP'
        for port in NFS_BLOCK_PORTS
        for proto in ('tcp', 'udp')
    ]


def peer_network_drop_specs(peer_address: str) -> list[str]:
    """Drop all traffic to and from the peer, like unplugging the cable between the hosts.

    Blocking only the xhad heartbeat is not enough: both hosts still see the statefile
    and none of them fences.
    """
    return [
        f'OUTPUT -d {peer_address} -j DROP',
        f'INPUT -s {peer_address} -j DROP',
    ]


def insert_rule(host: Host, rule_spec: str) -> None:
    logging.info(f'iptables -I {rule_spec} on {host}')
    host.ssh(f'iptables -I {rule_spec}')


def delete_rule(host: Host, rule_spec: str) -> None:
    if not host.is_ssh_reachable():
        return
    logging.info(f'iptables -D {rule_spec} on {host}')
    host.ssh(f'iptables -D {rule_spec}', check=False)


def install_persistent_rules(host: Host, rule_specs: list[str]) -> None:
    logging.info(f'Install persistent iptables rules on {host}')
    # An existing backup comes from an aborted run: it is the original config.
    host.ssh(f'[ -f {IPTABLES_BACKUP} ] || cp -a {IPTABLES_CONF} {IPTABLES_BACKUP}')
    for spec in rule_specs:
        insert_rule(host, spec)
    host.ssh('service iptables save')


def clear_persistent_rules(host: Host) -> None:
    """Restore the config saved by install_persistent_rules(), if any."""
    logging.info(f'Restore original iptables config on {host}')
    host.ssh(
        f'if [ -f {IPTABLES_BACKUP} ]; then '
        f'mv -f {IPTABLES_BACKUP} {IPTABLES_CONF} && iptables-restore < {IPTABLES_CONF}; fi'
    )
