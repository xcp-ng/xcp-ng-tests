import logging
import time

import lib.commands as commands
from lib.common import (
    GiB,
    prefix_object_name,
    safe_split,
    strtobool,
    wait_for,
    wait_for_not,
)
from lib.vdi import VDI

from typing import Optional

class SR:
    def __init__(self, uuid, pool):
        self.uuid = uuid
        self.pool = pool
        self._is_shared = None # cached value for is_shared()
        self._main_host = None # cached value for main_host()
        self._type = None # cache value for get_type()

    def pbd_uuids(self):
        return safe_split(self.pool.master.xe('pbd-list', {'sr-uuid': self.uuid}, minimal=True))

    def pbd_for_host(self, host):
        return safe_split(self.pool.master.xe(
            'pbd-list',
            {'sr-uuid': self.uuid, 'host-uuid': host.uuid},
            minimal=True
        ))[0]

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
            all_attached = all_attached and strtobool(self.pool.master.xe('pbd-param-get',
                                                                          {'uuid': pbd_uuid,
                                                                           'param-name': 'currently-attached',
                                                                           }))
        return all_attached

    def plug_pbd(self, pbd_uuid):
        self.pool.master.xe('pbd-plug', {'uuid': pbd_uuid})

    def plug_pbds(self, verify=True):
        logging.info("Attach PBDs")
        for pbd_uuid in self.pbd_uuids():
            self.plug_pbd(pbd_uuid)
        if verify:
            wait_for(self.all_pbds_attached, "Wait for PBDs attached")

    def vdi_uuids(self, managed=False, name_label=None):
        args = {
            'sr-uuid': self.uuid,
            'managed': managed
        }
        if name_label is not None:
            args['name-label'] = name_label
        return safe_split(self.pool.master.xe('vdi-list', args, minimal=True))

    def destroy(self, verify=False, force=False):
        logging.info(f"Will attempt SR destroy on {self.uuid}...")
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
                            if i == max_tries - 1:
                                # We tried already 4 times to destroy the SR, and there still are hidden VDIs that
                                # couldn't be force-GCed. In this case, we likely need to give time to the normal GC
                                # to run, which might also coalesce some VDIs if that's what it really needs.
                                # The GC should kick approximately 5 minutes after the last operation we did, so let's
                                # give it these 5 minutes plus extra time to complete.
                                gc_delay = 600
                                logging.warning(f"SR destroy failed {i} times in a row. "
                                                f"Wait for {gc_delay}s, hoping GC fully runs before next try")
                                time.sleep(gc_delay)
                            logging.info("Retrying sr-destroy in case it previously failed due to incomplete GC.")
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
            self._is_shared = strtobool(self.pool.master.xe('sr-param-get',
                                                            {'uuid': self.uuid, 'param-name': 'shared'}))
        return self._is_shared

    def get_type(self) -> str:
        if self._type is None:
            self._type = self.pool.master.xe("sr-param-get", {"uuid": self.uuid, "param-name": "type"})
        return self._type

    def create_vdi(self, name_label: str, virtual_size: int = 1 * GiB, image_format: Optional[str] = None) -> VDI:
        logging.info("Create VDI %r on SR %s", name_label, self.uuid)
        args = {
            'name-label': prefix_object_name(name_label),
            'virtual-size': str(virtual_size),
            'sr-uuid': self.uuid,
        }
        if image_format:
            args["sm-config:image-format"] = image_format
        vdi_uuid = self.pool.master.xe('vdi-create', args)
        return VDI(vdi_uuid, sr=self)

    def run_quicktest(self):
        logging.info(f"Run quicktest on SR {self.uuid}")
        # Always display the output of quicktest, failed or not.
        # This will duplicate the output in some cases, but it ensures we always have it for failure analysis,
        # even when quicktest leaves SRs in a state which makes teardown fail (in this case, pytest often doesn't
        # manage to display the details of the failed command, for a reason unknown - no usable reproducer found)
        try:
            output = self.pool.master.ssh(['/opt/xensource/debug/quicktest', '-sr', self.uuid])
            logging.info(f"Quicktest output: {output}")
        except commands.SSHCommandFailed as e:
            logging.error(f"Quicktest output: {e.stdout}")
            raise
