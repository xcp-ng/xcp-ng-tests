import socket

from lib.commands import local_cmd
from lib.common import wait_for

def wait_for_tcp_port(host: str, port: int, port_desc: str, ping: bool = True, host_desc: str | None = None) -> None:
    host_desc = f" ({host_desc})" if host_desc else ""
    if ping:
        wait_for(lambda: local_cmd(['ping', '-c1', host], check=False, simple_output=False).returncode == 0,
                 "Wait for host up (ICMP ping)", timeout_secs=10 * 60, retry_delay_secs=10)
    wait_for(lambda: local_cmd(['nc', '-zw5', host, str(port)], check=False, simple_output=False).returncode == 0,
             f"Wait for {port_desc} up on host{host_desc}", timeout_secs=10 * 60, retry_delay_secs=5)

def wait_for_ssh(host: str, host_desc: str | None = None, ping: bool = True) -> None:
    wait_for_tcp_port(host, 22, "SSH", ping, host_desc)

def is_ipv6(ip: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET6, ip)
        return True
    except Exception:
        return False

def wrap_ip(ip: str) -> str:
    """ Wrap an IP between brackets if and only if it's an IPv6. """
    return f"[{ip}]" if is_ipv6(ip) else ip
