import logging

from typing import Any, Literal, Optional, overload, TYPE_CHECKING, List

import lib.commands as commands
if TYPE_CHECKING:
    import lib.host

from lib.common import _param_add, _param_clear, _param_get, _param_remove, _param_set
from lib.sr import SR
from lib.vdi import VDI

class BaseVM:
    """ Base class for VM and Snapshot. """

    xe_prefix = "vm"
    uuid: str

    def __init__(self, uuid: str, host: 'lib.host.Host'):
        logging.info("New %s: %s", type(self).__name__, uuid)
        self.uuid = uuid
        self.host = host
        try:
            self.vdis = [VDI(vdi_uuid, host=host) for vdi_uuid in self.vdi_uuids()]
        except commands.SSHCommandFailed as e:
            # Doesn't work with Dom0 since `vm-disk-list` doesn't work on it so we create empty list
            if e.stdout == "Error: No matching VMs found":
                logging.info("Couldn't get disks list. We are Dom0. Continuing...")
                self.vdis = []
            else:
                raise

    @overload
    def param_get(self, param_name: str, key: Optional[str] = ...,
                  accept_unknown_key: Literal[False] = ...) -> str:
        ...

    @overload
    def param_get(self, param_name: str, key: Optional[str] = ...,
                  accept_unknown_key: Literal[True] = ...) -> Optional[str]:
        ...

    def param_get(self, param_name: str, key: Optional[str] = None,
                  accept_unknown_key: bool = False) -> Optional[str]:
        return _param_get(self.host, self.xe_prefix, self.uuid,
                          param_name, key, accept_unknown_key)

    def param_set(self, param_name: str, value: Any, key: Optional[str] = None) -> None:
        _param_set(self.host, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_remove(self, param_name: str, key: str, accept_unknown_key=False) -> None:
        _param_remove(self.host, self.xe_prefix, self.uuid,
                      param_name, key, accept_unknown_key)

    def param_add(self, param_name: str, value: str, key=None) -> None:
        _param_add(self.host, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_clear(self, param_name: str) -> None:
        _param_clear(self.host, self.xe_prefix, self.uuid,
                     param_name)

    def name(self) -> str:
        n = self.param_get('name-label')
        assert isinstance(n, str)
        return n

    def connect_vdi(self, vdi: VDI, device: str = "autodetect") -> str:
        logging.info(f">> Plugging VDI {vdi.uuid} on VM {self.uuid}")
        vbd_uuid = self.host.xe("vbd-create",
                                {
                                    "vdi-uuid": vdi.uuid,
                                    "vm-uuid": self.uuid,
                                    "device": device,
                                })
        self.host.xe("vbd-plug", {"uuid": vbd_uuid})

        self.vdis.append(vdi)

        return vbd_uuid

    def disconnect_vdi(self, vdi: VDI):
        logging.info(f"<< Unplugging VDI {vdi.uuid} from VM {self.uuid}")
        vbd_uuid = self.host.xe("vbd-list", {"vdi-uuid": vdi.uuid, "vm-uuid": self.uuid}, minimal=True)
        self.host.xe("vbd-unplug", {"uuid": vbd_uuid})
        self.host.xe("vbd-destroy", {"uuid": vbd_uuid})

    # @abstractmethod
    def _disk_list(self):
        raise NotImplementedError()

    def vdi_uuids(self, sr_uuid=None) -> List[str]:
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

    def destroy_vdi(self, vdi_uuid: str) -> None:
        self.host.xe('vdi-destroy', {'uuid': vdi_uuid})
        for vdi in self.vdis:
            if vdi.uuid == vdi_uuid:
                self.vdis.remove(vdi)

    def all_vdis_on_host(self, host):
        for vdi_uuid in self.vdi_uuids():
            sr = SR(self.host.pool.get_vdi_sr_uuid(vdi_uuid), self.host.pool)
            if not sr.attached_to_host(host):
                return False
        return True

    def all_vdis_on_sr(self, sr) -> bool:
        for vdi_uuid in self.vdi_uuids():
            if self.host.pool.get_vdi_sr_uuid(vdi_uuid) != sr.uuid:
                return False
        return True

    def get_sr(self):
        # in this method we assume the SR of the first VDI is the VM SR
        vdis = self.vdi_uuids()
        assert len(vdis) > 0, "Don't ask for the SR of a VM without VDIs!"
        sr = SR(self.host.pool.get_vdi_sr_uuid(vdis[0]), self.host.pool)
        assert sr.attached_to_host(self.host)
        return sr

    def export(self, filepath, compress='none', use_cache=False) -> None:
        logging.info("Export VM %s to %s with compress=%s" % (self.uuid, filepath, compress))
        params = {
            'uuid': self.uuid,
            'compress': compress,
            'filename': filepath
        }
        self.host.xe('vm-export', params)
