import logging
import traceback

import lib.commands as commands

from lib.common import safe_split, wait_for, wait_for_not
from lib.host import Host
from lib.sr import SR

class Pool:
    def __init__(self, master_hostname_or_ip):
        master = Host(self, master_hostname_or_ip)
        assert master.is_master(), f"Host {master_hostname_or_ip} is not a master host. Aborting."
        self.master = master
        self.hosts = [master]
        for host_uuid in self.hosts_uuids():
            if host_uuid != self.hosts[0].uuid:
                host = Host(self, self.host_ip(host_uuid))
                self.hosts.append(host)
        self.uuid = self.master.xe('pool-list', minimal=True)
        self.saved_uefi_certs = None

    def exec_on_hosts_on_error_rollback(self, func, rollback_func, host_list=[]):
        """
        Execute a function on all hosts of the pool.

        If something fails: execute rollback_func on each host on which the command was already executed.
        Then stop.
        Designed mainly for use in fixture setups that alter the hosts state.
        """
        hosts_done = []
        hosts = host_list if host_list else self.hosts
        for h in hosts:
            try:
                func(h)
                hosts_done.append(h)
            except Exception as e:
                if rollback_func:
                    logging.warning(
                        f"An error occurred in `exec_on_hosts_on_error_rollback` for host {h}\n"
                        f"Backtrace:\n{traceback.format_exc()}"
                    )
                    rollback_hosts = hosts_done + [h]

                    logging.info("Attempting to run the rollback function on host(s) "
                                 f"{', '.join([str(h) for h in rollback_hosts])}...")
                    try:
                        self.exec_on_hosts_on_error_continue(rollback_func, rollback_hosts)
                    except Exception:
                        pass
                raise e

    def exec_on_hosts_on_error_continue(self, func, host_list=[]):
        """
        Execute a function on all hosts of the pool.

        If something fails: store the exception but still attempt the function on the next hosts.
        Designed mainly for use in fixture teardowns.
        """
        errors = {}
        hosts = host_list if host_list else self.hosts
        for h in hosts:
            try:
                func(h)
            except Exception as e:
                logging.warning(
                    f"An error occurred in `exec_on_hosts_on_error_continue` for host {h}\n"
                    f"Backtrace:\n{traceback.format_exc()}"
                )
                logging.info("Attempting to run the function on the next hosts of the pool if there are any left...")
                errors[h.hostname_or_ip] = e
        if errors:
            raise Exception(f"One or more exceptions were raised in `exec_on_hosts_on_error_continue`: {errors}")

    def hosts_uuids(self):
        return safe_split(self.master.xe('host-list', {}, minimal=True))

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
        uuids = safe_split(self.master.xe('sr-list', {'shared': True, 'content-type': 'user'}, minimal=True))
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
            host.ssh(['rm', '-f', f'{host.varstore_dir()}/*'])

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

    def eject_host(self, host):
        master = self.master
        master.xe('pool-eject', {'host-uuid': host.uuid, 'force': True})
        wait_for_not(lambda: host.uuid in self.hosts_uuids(), f"Wait for host {host} to be ejected of pool {master}.")
        self.hosts = [h for h in self.hosts if h.uuid != host.uuid]
        wait_for(host.is_enabled, f"Wait for host {host} to restart in its own pool.", timeout_secs=600)
