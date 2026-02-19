from __future__ import annotations

import logging

from lib.basevm import BaseVM

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host
    from lib.vm import VM

class Snapshot(BaseVM):
    basevm: VM

    def __init__(self, uuid: str, host: Host, vm: VM):
        self.basevm = vm
        super(Snapshot, self).__init__(uuid, host)

    def _disk_list(self) -> str:
        return self.host.xe('snapshot-disk-list', {'uuid': self.uuid, 'vbd-params': ''},
                            minimal=True)

    def destroy(self, verify: bool = False) -> None:
        logging.info("Delete snapshot " + self.uuid)
        # that uninstall command apparently works better for snapshots than for VMs
        self.host.xe('snapshot-uninstall', {'uuid': self.uuid, 'force': True})
        if verify:
            logging.info("Check snapshot doesn't exist anymore")
            assert not self.exists()

    def exists(self) -> bool:
        return self.host.pool_has_vm(self.uuid, vm_type='snapshot')

    def revert(self) -> None:
        logging.info("Revert to snapshot %s", self.uuid)
        self.host.xe('snapshot-revert', {'uuid': self.uuid})
        self.basevm.create_vdis_list() # We reset the base VM object VDIs list because it changed following the revert
