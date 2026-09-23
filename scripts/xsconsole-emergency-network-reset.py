import http.client
import socket
import xmlrpc.client
import argparse

SOCKET_PATH = "/var/xapi/xmlrpcsocket.xsconsole"


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


class _UnixTransport(xmlrpc.client.Transport):
    def __init__(self, path, timeout):
        super().__init__()
        self._path, self._timeout = path, timeout

    def make_connection(self, host):
        return _UnixConnection(self._path, self._timeout)

try:
    parser = argparse.ArgumentParser()

    parser.add_argument('reset', type=int, help="Reset the NICs")
    args = parser.parse_args()

    c = xmlrpc.client.ServerProxy(
        "http://localhost/", transport=_UnixTransport(SOCKET_PATH, 10), allow_none=True
    )
    c.authenticate("REPLACE-PASSWORD")

    c.new("tester")

    # Network and Management Interface
    c.keypress("KEY_DOWN")
    c.keypress("KEY_ENTER")

    # Emergency Network Reset
    c.keypress("KEY_UP")
    c.keypress("KEY_ENTER")

    # Continue
    c.keypress("KEY_ENTER")

    # Ok
    c.keypress("KEY_ENTER")
    c.keypress("KEY_ENTER")

    # Reset (or not) the NICs
    if args.reset == 0:
        c.keypress("KEY_DOWN")

    c.keypress("KEY_ENTER")

    # DHCP
    c.keypress("KEY_ENTER")

    # Apply Changes and Reboot
    c.keypress("KEY_ENTER")

except Exception as e:
    print(e)

