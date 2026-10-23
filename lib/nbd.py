"""
A minimal NBD client, to test the NBD servers of XCP-ng from the test runner.

It implements what xapi-nbd needs: the fixed newstyle handshake, NBD_OPT_STARTTLS,
NBD_OPT_EXPORT_NAME (xapi-nbd does not support NBD_OPT_GO) and simple replies to
NBD_CMD_READ. See https://github.com/NetworkBlockDevice/nbd/blob/master/doc/proto.md

xapi-nbd only accepts connections arriving on the networks with the `nbd` (TLS) or
`insecure_nbd` purpose: the client can't run in dom0.
"""
from __future__ import annotations

import json
import logging
import socket
import ssl
import struct
import tempfile
from dataclasses import dataclass

from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from types import TracebackType

    from lib.host import Host
    from lib.vdi import VDI

NBD_MAGIC = b'NBDMAGIC'
NBD_OPTS_MAGIC = b'IHAVEOPT'
NBD_OPT_REPLY_MAGIC = 0x3e889045565a9
NBD_REQUEST_MAGIC = 0x25609513
NBD_SIMPLE_REPLY_MAGIC = 0x67446698

NBD_FLAG_FIXED_NEWSTYLE = 1 << 0
NBD_FLAG_NO_ZEROES = 1 << 1

NBD_OPT_EXPORT_NAME = 1
NBD_OPT_STARTTLS = 5
NBD_REP_ACK = 1

NBD_CMD_READ = 0
NBD_CMD_DISC = 2

class NbdError(Exception):
    pass

@dataclass
class NbdInfo:
    """ An entry of VDI.get_nbd_info. """
    address: str
    port: int
    exportname: str
    cert: str
    subject: str

class XapiNbdSession:
    """
    A XAPI session to give to xapi-nbd.

    The export names given by VDI.get_nbd_info embed the session, which xapi-nbd checks
    when a client connects: it must stay open as long as the NBD connections need it.
    """

    def __init__(self, host: Host):
        self.host = host
        self.ref = host.execute_script(
            "import XenAPI\n"
            "session = XenAPI.xapi_local()\n"
            "session.xenapi.login_with_password('root', '', '', 'xcp-ng-tests NBD session')\n"
            "print(session._session)\n",
            shebang='python3',
        )

    def nbd_info(self, vdi: VDI) -> list[NbdInfo]:
        output = self.host.execute_script(
            "import json\n"
            "import XenAPI\n"
            "session = XenAPI.xapi_local()\n"
            f"session._session = '{self.ref}'\n"
            f"vdi = session.xenapi.VDI.get_by_uuid('{vdi.uuid}')\n"
            "print(json.dumps(session.xenapi.VDI.get_nbd_info(vdi)))\n",
            shebang='python3',
        )
        return [NbdInfo(address=info['address'], port=int(info['port']), exportname=info['exportname'],
                        cert=info['cert'], subject=info['subject'])
                for info in json.loads(output)]

    def logout(self) -> None:
        self.host.execute_script(
            "import XenAPI\n"
            "session = XenAPI.xapi_local()\n"
            f"session.session.logout('{self.ref}')\n",
            shebang='python3',
        )

class NbdClient:
    """
    A connection to an NBD export.

    Requests can be pipelined: send_read() several times, then get their replies in order
    with recv_read(). The socket timeout applies to each operation on the socket.
    """

    def __init__(self, address: str, port: int, export_name: str, *, cert: str | None = None,
                 tls_hostname: str | None = None, timeout: float = 60):
        self.export_name = export_name
        self._next_handle = 0
        self._pending: dict[int, int] = {}  # handle -> length of the reply data
        self.sock: socket.socket = socket.create_connection((address, port), timeout=timeout)
        try:
            self._handshake(cert, tls_hostname)
        except BaseException:
            self.sock.close()
            raise

    @classmethod
    def from_nbd_info(cls, info: NbdInfo, timeout: float = 60) -> Self:
        return cls(info.address, info.port, info.exportname, cert=info.cert, tls_hostname=info.subject,
                   timeout=timeout)

    def _recv_exactly(self, length: int) -> bytes:
        buf = bytearray()
        while len(buf) < length:
            chunk = self.sock.recv(length - len(buf))
            if not chunk:
                raise NbdError(f"connection closed by the server after {len(buf)}/{length} bytes")
            buf += chunk
        return bytes(buf)

    def _send_option(self, option: int, data: bytes = b'') -> None:
        self.sock.sendall(NBD_OPTS_MAGIC + struct.pack('>II', option, len(data)) + data)

    def _handshake(self, cert: str | None, tls_hostname: str | None) -> None:
        if self._recv_exactly(8) != NBD_MAGIC or self._recv_exactly(8) != NBD_OPTS_MAGIC:
            raise NbdError("not a fixed newstyle NBD server")
        (server_flags,) = struct.unpack('>H', self._recv_exactly(2))
        if not server_flags & NBD_FLAG_FIXED_NEWSTYLE:
            raise NbdError(f"server does not support the fixed newstyle handshake (flags {server_flags:#x})")
        no_zeroes = bool(server_flags & NBD_FLAG_NO_ZEROES)
        self.sock.sendall(struct.pack('>I', NBD_FLAG_FIXED_NEWSTYLE | (NBD_FLAG_NO_ZEROES if no_zeroes else 0)))

        if cert is not None:
            self._send_option(NBD_OPT_STARTTLS)
            magic, option, reply, length = struct.unpack('>QIII', self._recv_exactly(20))
            if magic != NBD_OPT_REPLY_MAGIC or option != NBD_OPT_STARTTLS or reply != NBD_REP_ACK:
                raise NbdError(f"NBD_OPT_STARTTLS refused (reply {reply:#x})")
            self._recv_exactly(length)
            with tempfile.NamedTemporaryFile('w', suffix='.pem') as cafile:
                cafile.write(cert)
                cafile.flush()
                context = ssl.create_default_context(cafile=cafile.name)
            self.sock = context.wrap_socket(self.sock, server_hostname=tls_hostname)

        name = self.export_name.encode()
        self._send_option(NBD_OPT_EXPORT_NAME, name)
        self.size, self.transmission_flags = struct.unpack('>QH', self._recv_exactly(10))
        if not no_zeroes:
            self._recv_exactly(124)
        logging.debug("NBD export %s: %d bytes, flags %#x", self.export_name, self.size, self.transmission_flags)

    def send_read(self, offset: int, length: int) -> int:
        handle = self._next_handle
        self._next_handle += 1
        self.sock.sendall(struct.pack('>IHHQQI', NBD_REQUEST_MAGIC, 0, NBD_CMD_READ, handle, offset, length))
        self._pending[handle] = length
        return handle

    def recv_read(self) -> tuple[int, bytes]:
        """ Receive the next reply to a read: returns its handle and its data. """
        magic, error, handle = struct.unpack('>IIQ', self._recv_exactly(16))
        if magic != NBD_SIMPLE_REPLY_MAGIC:
            raise NbdError(f"bad reply magic {magic:#x}")
        length = self._pending.pop(handle, None)
        if length is None:
            raise NbdError(f"reply for an unknown handle {handle}")
        if error:
            raise NbdError(f"read failed with error {error}")
        return handle, self._recv_exactly(length)

    def read(self, offset: int, length: int) -> bytes:
        handle = self.send_read(offset, length)
        reply_handle, data = self.recv_read()
        assert reply_handle == handle
        return data

    def abort(self) -> None:
        """ Shut the connection down, waking up a thread blocked on it. """
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def close(self) -> None:
        try:
            if not self._pending:
                self.sock.sendall(struct.pack('>IHHQQI', NBD_REQUEST_MAGIC, 0, NBD_CMD_DISC, 0, 0, 0))
        except OSError:
            pass
        finally:
            self.sock.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()
