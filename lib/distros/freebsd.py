from __future__ import annotations

import logging

from lib.unixvm import UnixVM

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host


class FreeBsdVM(UnixVM):

    def __init__(self, uuid: str, host: 'Host'):
        super().__init__(uuid, host)

    def configure_serial_console(self):
        """ TODO: Implement FreeBSD serial console configuration. """
        logging.warning("Serial console configuration for FreeBSD not yet implemented")
