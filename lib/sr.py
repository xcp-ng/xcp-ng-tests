import logging

import lib.commands as commands

from lib.common import wait_for, wait_for_not

class SR:
    def __init__(self, uuid, pool):
        self.uuid = uuid
        self.pool = pool

    def pbd_uuids(self):
        return self.pool.master.xe('pbd-list', {'sr-uuid': self.uuid}, minimal=True).split(',')

    def unplug_pbds(self, force=False):
        logging.info("Unplug PBDs")
        for pbd_uuid in self.pbd_uuids():
            try:
                self.pool.master.xe('pbd-unplug', {'uuid': pbd_uuid})
            except commands.SSHCommandFailed as e:
                # We must be sure to execute correctly "unplug" on unplugged VDIs without error
                # if force is set.
                if not force:
                    raise
                logging.warning('Ignore exception during PBD unplug: {}'.format(e))

    def all_pbds_attached(self):
        all_attached = True
        for pbd_uuid in self.pbd_uuids():
            all_attached = all_attached and self.pool.master.xe('pbd-param-get', {'uuid': pbd_uuid,
                                                                'param-name': 'currently-attached'})
        return all_attached

    def plug_pbds(self, verify=True):
        logging.info("Attach PBDs")
        for pbd_uuid in self.pbd_uuids():
            self.pool.master.xe('pbd-plug', {'uuid': pbd_uuid})
        if verify:
            wait_for(self.all_pbds_attached, "Wait for PDBs attached")

    def destroy(self, verify=False, force=False):
        self.unplug_pbds(force)
        logging.info("Destroy SR " + self.uuid)
        self.pool.master.xe('sr-destroy', {'uuid': self.uuid})
        if verify:
            wait_for_not(self.exists, "Wait for SR destroyed")

    def forget(self, force=False):
        self.unplug_pbds(force)
        logging.info("Forget SR " + self.uuid)
        self.pool.master.xe('sr-forget', {'uuid': self.uuid})

    def exists(self):
        return self.pool.master.xe('sr-list', {'uuid': self.uuid}, minimal=True) == self.uuid

    def scan(self):
        logging.info("Scan SR " + self.uuid)
        self.pool.master.xe('sr-scan', {'uuid': self.uuid})

    def hosts_uuids(self):
        return self.pool.master.xe('pbd-list', {'sr-uuid': self.uuid, 'params': 'host-uuid'}, minimal=True).split(',')

    def attached_to_host(self, host):
        return host.uuid in self.hosts_uuids()

    def main_host(self):
        """ Returns the host in case of a local SR, the master host in case of a shared SR. """
        if self.is_shared():
            return self.pool.master
        else:
            return self.pool.get_host_by_uuid(self.hosts_uuids()[0])

    def content_type(self):
        return self.pool.master.xe('sr-param-get', {'uuid': self.uuid, 'param-name': 'content-type'})

    def is_shared(self):
        return self.pool.master.xe('sr-param-get', {'uuid': self.uuid, 'param-name': 'shared'})

    def force_gc(self):
        self.main_host().ssh(['/opt/xensource/sm/cleanup.py', '-u', self.uuid, '-G'])
