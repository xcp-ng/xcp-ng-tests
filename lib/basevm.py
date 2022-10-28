import logging

import lib.commands as commands

from lib.sr import SR

class BaseVM:
    """ Base class for VM and Snapshot. """

    def __init__(self, uuid, host):
        self.uuid = uuid
        self.host = host

    def param_get(self, param_name, key=None, accept_unknown_key=False):
        args = {'uuid': self.uuid, 'param-name': param_name}
        if key is not None:
            args['param-key'] = key
        try:
            value = self.host.xe('vm-param-get', args)
        except commands.SSHCommandFailed as e:
            if key and accept_unknown_key and e.stdout == "Error: Key %s not found in map" % key:
                value = None
            else:
                raise
        return value

    def param_set(self, param_name, key, value):
        args = {'uuid': self.uuid}

        if key is not None:
            param_name = '{}:{}'.format(param_name, key)

        args[param_name] = value

        return self.host.xe('vm-param-set', args)

    def param_remove(self, param_name, key=None, accept_unknown_key=False):
        args = {'uuid': self.uuid, 'param-name': param_name}
        if key is not None:
            args['param-key'] = key
        try:
            self.host.xe('vm-param-remove', args)
        except commands.SSHCommandFailed as e:
            if key and accept_unknown_key and e.stdout == "Error: Key %s not found in map" % key:
                pass
            else:
                raise

    def name(self):
        return self.param_get('name-label')

    def vdi_uuids(self):
        output = self._disk_list()
        vdis = []
        for line in output.splitlines():
            vdis.append(line.split(',')[0])
        return vdis

    def destroy_vdi(self, vdi_uuid):
        self.host.xe('vdi-destroy', {'uuid': vdi_uuid})

    # FIXME: move this method and the above back to class VM if not useful in Snapshot class?
    def destroy(self):
        for vdi_uuid in self.vdi_uuids():
            self.destroy_vdi(vdi_uuid)
        self._destroy()

    def get_vdi_sr_uuid(self, vdi_uuid):
        return self.host.xe('vdi-param-get', {'uuid': vdi_uuid, 'param-name': 'sr-uuid'})

    def all_vdis_on_host(self, host):
        for vdi_uuid in self.vdi_uuids():
            sr = SR(self.get_vdi_sr_uuid(vdi_uuid), self.host.pool)
            if not sr.attached_to_host(host):
                return False
        return True

    def all_vdis_on_sr(self, sr):
        for vdi_uuid in self.vdi_uuids():
            if self.get_vdi_sr_uuid(vdi_uuid) != sr.uuid:
                return False
        return True

    def get_sr(self):
        # in this method we assume the SR of the first VDI is the VM SR
        vdis = self.vdi_uuids()
        assert len(vdis) > 0, "Don't ask for the SR of a VM without VDIs!"
        sr = SR(self.get_vdi_sr_uuid(vdis[0]), self.host.pool)
        assert sr.attached_to_host(self.host)
        return sr

    def export(self, filepath, compress='none'):
        logging.info("Export VM %s to %s with compress=%s" % (self.uuid, filepath, compress))
        params = {
            'uuid': self.uuid,
            'compress': compress,
            'filename': filepath
        }
        self.host.xe('vm-export', params)
