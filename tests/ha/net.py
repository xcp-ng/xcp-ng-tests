from __future__ import annotations

import lib.commands as commands
from lib.host import Host


def address_is_reachable(hostname_or_ip: str) -> bool:
    return commands.local_cmd(
        ['nc', '-zw5', str(hostname_or_ip), '22'],
        check=False,
    ).returncode == 0


def host_is_reachable(host: Host) -> bool:
    return address_is_reachable(str(host.hostname_or_ip))
