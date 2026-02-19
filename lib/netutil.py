import socket

def is_ipv6(ip: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET6, ip)
        return True
    except Exception:
        return False

def wrap_ip(ip: str) -> str:
    """ Wrap an IP between brackets if and only if it's an IPv6. """
    return f"[{ip}]" if is_ipv6(ip) else ip
