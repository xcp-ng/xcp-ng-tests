import logging

from lib.basevm import BaseVM

class Snapshot(BaseVM):
    def _disk_list(self):
        return self.host.xe('snapshot-disk-list', {'uuid': self.uuid}, minimal=True)

    def destroy(self, verify=False):
        logging.info("Delete snapshot " + self.uuid)
        # that uninstall command apparently works better for snapshots than for VMs
        self.host.xe('snapshot-uninstall', {'uuid': self.uuid, 'force': True})
        if verify:
            logging.info("Check snapshot doesn't exist anymore")
            assert not self.exists()

    def exists(self):
        return self.host.pool_has_vm(self.uuid, vm_type='snapshot')

    def revert(self):
        logging.info("Revert to snapshot %s", self.uuid)
        self.host.xe('snapshot-revert', {'uuid': self.uuid})
