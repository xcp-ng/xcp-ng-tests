from lib.host import Host

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host > 8.2.

def test_get_hyperthreading(host: Host) -> None:
    host.call_plugin('hyperthreading.py', 'get_hyperthreading')
