from __future__ import annotations

import logging

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host

from lib.common import XeObject, XeParams
from lib.sr import SR

class BaseVM(XeObject):
    """ Base class for VM and Snapshot. """

    xe_prefix = "vm"
    uuid: str

    def __init__(self, uuid: str, host: Host):
        logging.info("New %s: %s", type(self).__name__, uuid)
        super().__init__(host, uuid)
        self.host = host

    def name(self) -> str:
        n = self.param_get('name-label')
        assert isinstance(n, str)
        return n

    def description(self) -> str:
        description = self.param_get('name-description')
        assert isinstance(description, str)
        return description

    # @abstractmethod
    def _disk_list(self) -> str:
        raise NotImplementedError()

    def vdi_uuids(self, sr_uuid: str | None = None) -> list[str]:
        output = self._disk_list()
        if output == '':
            return []

        vdis = output.split(',')

        if sr_uuid is None:
            return vdis

        vdis_on_sr = []
        for vdi in vdis:
            if self.host.pool.get_vdi_sr_uuid(vdi) == sr_uuid:
                vdis_on_sr.append(vdi)
        return vdis_on_sr

    def all_vdis_on_host(self, host: Host) -> bool:
        for vdi_uuid in self.vdi_uuids():
            sr = SR(self.host.pool.get_vdi_sr_uuid(vdi_uuid), self.host.pool)
            if not sr.attached_to_host(host):
                return False
        return True

    def all_vdis_on_sr(self, sr: SR) -> bool:
        return all(self.host.pool.get_vdi_sr_uuid(vdi_uuid) == sr.uuid for vdi_uuid in self.vdi_uuids())

    def get_sr(self) -> SR:
        # in this method we assume the SR of the first VDI is the VM SR
        vdis = self.vdi_uuids()
        assert len(vdis) > 0, "Don't ask for the SR of a VM without VDIs!"
        sr = SR(self.host.pool.get_vdi_sr_uuid(vdis[0]), self.host.pool)
        assert sr.attached_to_host(self.host)
        return sr

    def export(self, filepath: str, compress: str = 'none', use_cache: bool = False) -> None:
        logging.info("Export VM %s to %s with compress=%s" % (self.uuid, filepath, compress))
        params: XeParams = {
            'uuid': self.uuid,
            'compress': compress,
            'filename': filepath
        }
        self.host.xe('vm-export', params)
