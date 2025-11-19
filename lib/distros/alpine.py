from __future__ import annotations

from lib.unixvm import UnixVM

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host


class AlpineVM(UnixVM):

    def __init__(self, uuid: str, host: 'Host'):
        super().__init__(uuid, host)

    def configure_serial_console(self):

        # Try extlinux.conf first (common in Alpine)
        self.ssh('export F=/boot/extlinux.conf ; '
                 '[ -f ${F} ] && '
                 ' ( grep -q "APPEND.*console=ttyS" ${F} || '
                 '   sed -i "/APPEND.*root=/ { s/$/ console=ttyS,115200/; s/ quiet / /}" ${F} )',
                 check=False)

        # Also try GRUB (Alpine UEFI - alpine-mini)
        self.ssh(
            'export F=/etc/default/grub ; '
            '[ -f ${F} ] && '
            ' ( grep -q " console=ttyS" ${F} || '
            '   sed -i \'/^GRUB_CMDLINE_LINUX_DEFAULT/ { s/ quiet / /; '
            '             s/ *"$/ console=ttyS,115200"/ }\' ${F} ) && '
            ' grub-mkconfig -o /boot/grub/grub.cfg',
            check=False
        )
