import logging

import lib.commands as commands

from lib.host import Host
from lib.sr import SR

class Pool:
    def __init__(self, master):
        self.master = master
        self.hosts = [master]
        for host_uuid in self.hosts_uuids():
            if host_uuid != self.hosts[0].uuid:
                host = Host(self.host_ip(host_uuid))
                host.initialize(pool=self)
                self.hosts.append(host)
        self.uuid = self.master.xe('pool-list', minimal=True)
        self.saved_uefi_certs = None

    def hosts_uuids(self):
        return self.master.xe('host-list', {}, minimal=True).split(',')

    def host_ip(self, host_uuid):
        return self.master.xe('host-param-get', {'uuid': host_uuid, 'param-name': 'address'})

    def get_host_by_uuid(self, host_uuid):
        for host in self.hosts:
            if host.uuid == host_uuid:
                return host
        raise Exception(f"Host with uuid {host_uuid} not found in pool.")

    def first_host_that_isnt(self, host):
        for h in self.hosts:
            if h != host:
                return h
        return None

    def first_shared_sr(self):
        uuids = self.master.xe('sr-list', {'shared': True, 'content-type': 'user'}, minimal=True).split(',')
        if len(uuids) > 0:
            return SR(uuids[0], self)
        return None

    def save_uefi_certs(self):
        logging.info('Saving pool UEFI certificates')

        if int(self.master.ssh(["secureboot-certs", "--version"]).split(".")[0]) < 1:
            raise RuntimeError("The host must have secureboot-certs version >= 1.0.0")

        saved_certs = {
            'PK': self.master.ssh(['mktemp']),
            'KEK': self.master.ssh(['mktemp']),
            'db': self.master.ssh(['mktemp']),
            'dbx': self.master.ssh(['mktemp'])
        }
        # save the pool certs in temporary files on master host
        for cert in list(saved_certs.keys()):
            tmp_file = saved_certs[cert]
            try:
                self.master.ssh(['secureboot-certs', 'extract', cert, tmp_file])
            except commands.SSHCommandFailed as e:
                if "does not exist in XAPI pool DB" in e.stdout:
                    # there's no cert to save
                    self.master.ssh(['rm', '-f', tmp_file])
                    del saved_certs[cert]
        # Either there are no certs at all, or there must be at least PK, KEK and db,
        # else we won't be able to restore the exact same state
        if len(saved_certs) == 0 or ('PK' in saved_certs and 'KEK' in saved_certs and 'db' in saved_certs):
            self.saved_uefi_certs = saved_certs
            logging.info('Pool UEFI certificates state saved: %s'
                         % (' '.join(saved_certs.keys()) if saved_certs else 'no certs'))
        else:
            for tmp_file in saved_certs.values():
                self.master.ssh(['rm', '-f', tmp_file])
            raise Exception(
                (
                    "Can't save pool UEFI certs. Only %s certs are defined, "
                    "which wouldn't be restorable as is with secureboot-certs install"
                )
                % ' & '.join(saved_certs.keys())
            )

    def restore_uefi_certs(self):
        assert self.saved_uefi_certs is not None
        if len(self.saved_uefi_certs) == 0:
            logging.info('We need to clear pool UEFI certificates to restore initial state')
            self.clear_uefi_certs()
        else:
            assert 'PK' in self.saved_uefi_certs and 'KEK' in self.saved_uefi_certs and 'db' in self.saved_uefi_certs
            logging.info('Restoring pool UEFI certificates: ' + ' '.join(self.saved_uefi_certs.keys()))
            # restore certs
            params = [self.saved_uefi_certs['PK'], self.saved_uefi_certs['KEK'], self.saved_uefi_certs['db']]
            if 'dbx' in self.saved_uefi_certs:
                params.append(self.saved_uefi_certs['dbx'])
            else:
                params.append('none')
            self.master.ssh(['secureboot-certs', 'install'] + params)
            # remove files from host
            for tmp_file in self.saved_uefi_certs.values():
                self.master.ssh(['rm', '-f', tmp_file])
            self.saved_uefi_certs = None

    def clear_uefi_certs(self):
        logging.info('Clearing pool UEFI certificates in XAPI and on hosts disks')
        self.master.ssh(['secureboot-certs', 'clear'])
        # remove files on each host
        for host in self.hosts:
            host.ssh(['rm', '-f', '/var/lib/uefistored/*'])

    def install_custom_uefi_certs(self, auths):
        host = self.master
        auths_dict = {}

        try:
            for auth in auths:
                tmp_file_on_host = host.ssh(['mktemp'])
                host.scp(auth.auth, tmp_file_on_host)
                auths_dict[auth.name] = tmp_file_on_host

            assert 'PK' in auths_dict
            assert 'KEK' in auths_dict
            assert 'db' in auths_dict

            logging.info('Installing auths to pool: %s' % list(auths_dict.keys()))
            for key in auths_dict.keys():
                value = host.ssh([f'md5sum {auths_dict[key]} | cut -d " " -f 1'])
                logging.debug('Key: %s, value: %s' % (key, value))
            params = [auths_dict['PK'], auths_dict['KEK'], auths_dict['db']]
            if 'dbx' in auths_dict:
                params.append(auths_dict['dbx'])
            else:
                params.append('none')

            host.ssh(['secureboot-certs', 'install'] + params)
        finally:
            host.ssh(['rm', '-f'] + list(auths_dict.values()))
