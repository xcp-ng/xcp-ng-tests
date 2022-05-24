import logging

import lib.commands as commands

from lib.common import safe_split, wait_for, wait_for_not

class SR:
    def __init__(self, uuid, pool):
        self.uuid = uuid
        self.pool = pool
        self._is_shared = None # cached value for is_shared()
        self._main_host = None # cached value for main_host()

    def pbd_uuids(self):
        return safe_split(self.pool.master.xe('pbd-list', {'sr-uuid': self.uuid}, minimal=True))[0]

    def pbd_for_host(self, host):
        return safe_split(self.pool.master.xe(
            'pbd-list',
            {'sr-uuid': self.uuid, 'host_uuid': host.uuid},
            minimal=True
        ))

    def unplug_pbd(self, pbd_uuid, force=False):
        try:
            self.pool.master.xe('pbd-unplug', {'uuid': pbd_uuid})
        except commands.SSHCommandFailed as e:
            # We must be sure to execute correctly "unplug" on unplugged VDIs without error
            # if force is set.
            if not force:
                raise
            logging.warning('Ignore exception during PBD unplug: {}'.format(e))

    def unplug_pbds(self, force=False):
        logging.info(f"Unplug PBDs for SR {self.uuid}")
        for pbd_uuid in self.pbd_uuids():
            self.unplug_pbd(pbd_uuid, force=force)

    def all_pbds_attached(self):
        all_attached = True
        for pbd_uuid in self.pbd_uuids():
            all_attached = all_attached and self.pool.master.xe('pbd-param-get', {'uuid': pbd_uuid,
                                                                'param-name': 'currently-attached'})
        return all_attached

    def plug_pbd(self, pbd_uuid):
        self.pool.master.xe('pbd-plug', {'uuid': pbd_uuid})

    def plug_pbds(self, verify=True):
        logging.info("Attach PBDs")
        for pbd_uuid in self.pbd_uuids():
            self.plug_pbd(pbd_uuid)
        if verify:
            wait_for(self.all_pbds_attached, "Wait for PDBs attached")

    def vdi_uuids(self, managed=False, name_label=None):
        args = {
            'sr-uuid': self.uuid,
            'managed': managed
        }
        if name_label is not None:
            args['name-label'] = name_label
        return safe_split(self.pool.master.xe('vdi-list', args, minimal=True))

    def destroy(self, verify=False, force=False):
        # Rescan SR to improve the chances of the forced GC run triggered by sr-destroy
        # remove all VDIs in one pass and such have sr-destroy working on first try.
        self.scan()
        max_tries = 5
        for i in range(1, max_tries + 1): # [1, 2, ..., max_tries]
            self.unplug_pbds(force)
            logging.info(f"Destroy SR {self.uuid} (attempt {i})")
            try:
                # Note: sr-destroy triggers ONE forced GC run
                # This may not be enough in some cases
                # (when VDIs to GC are not all leafs and would require several runs)
                self.pool.master.xe('sr-destroy', {'uuid': self.uuid})
            except commands.SSHCommandFailed as e:
                if "the SR is not empty" not in e.stdout:
                    raise
                else:
                    logging.info(f"SR destroy failed with message: {e.stdout}")
                    try:
                        self.plug_pbds()
                        # rescan for an up to date list of VDIs
                        self.scan()
                    except commands.SSHCommandFailed:
                        raise Exception("SR destroy failed and then pbd-plug failed too. Can't continue further.")
                    output = self.vdi_uuids(managed=True)
                    if len(output) > 0:
                        raise Exception("SR destroy failed due to SR not empty, "
                                        "and there are indeed managed VDIs left on the SR.")
                    else:
                        logging.info("SR destroy failed due to SR not empty but there aren't any managed VDIs left.")
                        if i < max_tries:
                            logging.info(f"Retrying sr-destroy in case it failed due to incomplete GC.")
                            continue
                        else:
                            raise Exception(f"Could not destroy the SR even after {i} attempts.")
            if verify:
                wait_for_not(self.exists, "Wait for SR destroyed")
            # Everything apparently went fine. Get out of the retry loop.
            break

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
        return safe_split(self.pool.master.xe('pbd-list', {'sr-uuid': self.uuid, 'params': 'host-uuid'}, minimal=True))

    def attached_to_host(self, host):
        return host.uuid in self.hosts_uuids()

    def main_host(self):
        """ Returns the host in case of a local SR, the master host in case of a shared SR. """
        if self._main_host is None:
            if self.is_shared():
                self._main_host = self.pool.master
            else:
                self._main_host = self.pool.get_host_by_uuid(self.hosts_uuids()[0])
        return self._main_host

    def content_type(self):
        return self.pool.master.xe('sr-param-get', {'uuid': self.uuid, 'param-name': 'content-type'})

    def is_shared(self):
        if self._is_shared is None:
            self._is_shared = self.pool.master.xe('sr-param-get', {'uuid': self.uuid, 'param-name': 'shared'})
        return self._is_shared
