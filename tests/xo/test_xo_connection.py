import logging

from lib.host import Host

# This test is meant to test an XO connection.
#
# Requirements:
# - an XCP-ng host >= 8.2 with latest updates.
# - An XOA registered through `xo-cli`

def test_xo_connection(hosts_with_xo: list[Host]) -> None:
    for h in hosts_with_xo:
        logging.debug(f"Testing connection for host: {h.hostname_or_ip}")
        assert h.xo_get_server_id() is not None
