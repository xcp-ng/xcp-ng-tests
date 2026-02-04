from __future__ import annotations

import logging
import os
import traceback

from packaging import version

import lib.commands as commands
from lib.common import HostAddress, _param_get, _param_set, safe_split, wait_for, wait_for_not
from lib.efi import EFIAuth
from lib.host import Host
from lib.sr import SR

from typing import Any, Callable, Iterable

class Pool:
    xe_prefix = "pool"

    def __init__(self, master_hostname_or_ip: HostAddress) -> None:
        master = Host(self, master_hostname_or_ip)
        assert master.is_master(), f"Host {master_hostname_or_ip} is not a master host. Aborting."
        self.master = master
        self.hosts = [master]

        # wait for XAPI startup to be done, or we can get "Connection
        # refused (calling connect )" when calling self.hosts_uuids()
        wait_for(lambda: commands.ssh_with_result(master_hostname_or_ip,
                                                  'xapi-wait-init-complete 60').returncode == 0,
                 f"Wait for XAPI init to be complete on {master_hostname_or_ip}",
                 timeout_secs=30 * 60)

        logging.info("Getting Pool info for %r", master_hostname_or_ip)
        for host_uuid in self.hosts_uuids():
            if host_uuid != self.hosts[0].uuid:
                host = Host(self, self.host_ip(host_uuid))
                self.hosts.append(host)
        self.uuid = self.master.xe('pool-list', minimal=True)
        self.saved_uefi_certs: dict[str, str] | None = None
        self.pre_existing_sr_uuids = safe_split(self.master.xe('sr-list', {'minimal': 'true'}), ',')

    def param_get(self, param_name: str, key: str | None = None, accept_unknown_key: bool = False) -> str | None:
        return _param_get(self.master, Pool.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

    def param_set(self, param_name: str, value: str | bool | dict[str, str], key: str | None = None) -> None:
        _param_set(self.master, Pool.xe_prefix, self.uuid, param_name, value, key)

    def exec_on_hosts_on_error_rollback(self, func: Callable[[Host], Any],
                                        rollback_func: Callable[[Host], Any] | None,
                                        host_list: list[Host] = []) -> None:
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

    def exec_on_hosts_on_error_continue(self, func: Callable[[Host], Any], host_list: list[Host] = []) -> None:
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

    def hosts_uuids(self) -> list[str]:
        return safe_split(self.master.xe('host-list', {}, minimal=True))

    def host_ip(self, host_uuid: str) -> str:
        return self.master.xe('host-param-get', {'uuid': host_uuid, 'param-name': 'address'})

    def get_host_by_uuid(self, host_uuid: str) -> Host:
        for host in self.hosts:
            if host.uuid == host_uuid:
                return host
        raise Exception(f"Host with uuid {host_uuid} not found in pool.")

    def first_host_that_isnt(self, host: Host) -> Host | None:
        for h in self.hosts:
            if h != host:
                return h
        return None

    def first_shared_sr(self) -> SR | None:
        uuids = safe_split(self.master.xe('sr-list', {'shared': True, 'content-type': 'user'}, minimal=True))
        if len(uuids) > 0:
            return SR(uuids[0], self)
        return None

    def get_vdi_sr_uuid(self, vdi_uuid: str) -> str:
        return self.master.xe('vdi-param-get', {'uuid': vdi_uuid, 'param-name': 'sr-uuid'})

    def get_iso_sr(self) -> SR:
        uuids = safe_split(self.master.xe('sr-list', {'type': 'iso',
                                                      'content-type': 'iso',
                                                      'is-tools-sr': False},
                                          minimal=True))
        assert len(uuids) == 1  # we may need to allow finer selection if this triggers
        return SR(uuids[0], self)

    def push_iso(self, local_file: str, remote_filename: str | None = None) -> str:
        iso_sr = self.get_iso_sr()
        mountpoint = f"/run/sr-mount/{iso_sr.uuid}"
        if remote_filename is None:
            # needs only work on XCP-ng 8.2+
            remote_filename = self.master.ssh(f'mktemp --suffix=.iso -p {mountpoint}')
            self.master.ssh(f'chmod 644 {remote_filename}')

        logging.info("Uploading to ISO-SR %s as %s", local_file, remote_filename)
        self.master.scp(local_file, remote_filename)
        iso_sr.scan()
        return os.path.basename(remote_filename)

    def remove_iso(self, remote_filename: str) -> None:
        iso_sr = self.get_iso_sr()
        fullpath = f"/run/sr-mount/{iso_sr.uuid}/{remote_filename}"
        logging.info("Removing %s from ISO-SR server", remote_filename)
        self.master.ssh(f'rm {fullpath}')

    def save_uefi_certs(self) -> None:
        """
        Save UEFI certificates in order to restore them later. XCP-ng 8.2 only.

        This method was developed for XCP-ng 8.2, because many secureboot tests were dependent
        on the initial state of the pool certificates, due to how certificates propagate.
        Also, there were no certificates installed by default (except PK) on XCP-ng 8.2, and
        we tried to be nice and restore the initial state after the tests.

        On XCP-ng 8.3+, the tests don't depend so much on the pool certificates, and when they do we
        can simply set custom certificates without erasing the default ones, so there's no real need
        for saving then restoring the certificates.
        The method was not reviewed for XCP-ng 8.3, and tests should be written in a way that is not
        dependent on the initial state of pool certificates. To prevent ourselves from using a method
        that is not appropriate, assert that the version is lower than 8.3.

        This can be revised later if a need for saving custom certificates in 8.3+ arises.
        """
        assert self.master.xcp_version < version.parse("8.3"), "this function should only be needed on XCP-ng 8.2"
        logging.info('Saving pool UEFI certificates')

        if int(self.master.ssh("secureboot-certs --version").split(".")[0]) < 1:
            raise RuntimeError("The host must have secureboot-certs version >= 1.0.0")

        saved_certs = {
            'PK': self.master.ssh('mktemp'),
            'KEK': self.master.ssh('mktemp'),
            'db': self.master.ssh('mktemp'),
            'dbx': self.master.ssh('mktemp')
        }
        # save the pool certs in temporary files on master host
        for cert in list(saved_certs.keys()):
            tmp_file = saved_certs[cert]
            try:
                self.master.ssh(f'secureboot-certs extract {cert} {tmp_file}')
            except commands.SSHCommandFailed as e:
                if "does not exist in XAPI pool DB" in e.stdout:
                    # there's no cert to save
                    self.master.ssh(f'rm -f {tmp_file}')
                    del saved_certs[cert]
        # Either there are no certs at all, or there must be at least PK, KEK and db,
        # else we won't be able to restore the exact same state
        if len(saved_certs) == 0 or ('PK' in saved_certs and 'KEK' in saved_certs and 'db' in saved_certs):
            self.saved_uefi_certs = saved_certs
            logging.info('Pool UEFI certificates state saved: %s'
                         % (' '.join(saved_certs.keys()) if saved_certs else 'no certs'))
        else:
            for tmp_file in saved_certs.values():
                self.master.ssh(f'rm -f {tmp_file}')
            raise Exception(
                (
                    "Can't save pool UEFI certs. Only %s certs are defined, "
                    "which wouldn't be restorable as is with secureboot-certs install"
                )
                % ' & '.join(saved_certs.keys())
            )

    def restore_uefi_certs(self) -> None:
        # See explanation in save_uefi_certs().
        assert self.master.xcp_version < version.parse("8.3"), "this function should only be needed on XCP-ng 8.2"
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
            self.master.ssh(' '.join(['secureboot-certs', 'install'] + params))
            # remove files from host
            for tmp_file in self.saved_uefi_certs.values():
                self.master.ssh(f'rm -f {tmp_file}')
            self.saved_uefi_certs = None

    def clear_uefi_certs(self) -> None:
        """
        Clear UEFI certificates on XCP-ng 8.2.

        On XCP-ng 8.2, clearing the certificates from XAPI doesn't clear them from disk, so we need to do so manually.

        This method is not suitable for XCP-ng 8.3+, where only custom certificates can be modified, and this
        must all be done through XAPI (which will delete them from disk on each host automatically).

        For XCP-ng 8.3+, see clear_custom_uefi_certificates()
        """
        assert self.master.xcp_version < version.parse("8.3"), "function only relevant on XCP-ng 8.2"
        logging.info('Clearing pool UEFI certificates in XAPI and on hosts disks')
        self.master.ssh('secureboot-certs clear')
        # remove files on each host
        for host in self.hosts:
            host.ssh(f'rm -f {host.varstore_dir()}/*')

    def clear_custom_uefi_certs(self) -> None:
        """ Clear Custom UEFI certificates on XCP-ng 8.3+. """
        assert self.master.xcp_version >= version.parse("8.3"), "function only relevant on XCP-ng 8.3+"
        logging.info('Clearing custom pool UEFI certificates')
        self.master.ssh('secureboot-certs clear')

    def install_custom_uefi_certs(self, auths: Iterable[EFIAuth]) -> None:
        host = self.master
        auths_dict: dict[str, str] = {}

        try:
            for auth in auths:
                tmp_file_on_host = host.ssh('mktemp')
                host.scp(auth.auth(), tmp_file_on_host)
                auths_dict[auth.name] = tmp_file_on_host

            assert 'PK' in auths_dict
            assert 'KEK' in auths_dict
            assert 'db' in auths_dict

            logging.info('Installing auths to pool: %s' % list(auths_dict.keys()))
            for key in auths_dict:
                value = host.ssh(f'md5sum {auths_dict[key]} | cut -d " " -f 1')
                logging.debug('Key: %s, value: %s' % (key, value))
            params = [auths_dict['PK'], auths_dict['KEK'], auths_dict['db']]
            if 'dbx' in auths_dict:
                params.append(auths_dict['dbx'])
            else:
                params.append('none')

            host.ssh(' '.join(['secureboot-certs', 'install'] + params))
        finally:
            host.ssh('rm -f ' + ' '.join(auths_dict.values()))

    def eject_host(self, host: Host) -> None:
        master = self.master
        master.xe('pool-eject', {'host-uuid': host.uuid, 'force': True})
        wait_for_not(lambda: host.uuid in self.hosts_uuids(), f"Wait for host {host} to be ejected of pool {master}.")
        self.hosts = [h for h in self.hosts if h.uuid != host.uuid]
        wait_for(host.is_enabled, f"Wait for host {host} to restart in its own pool.", timeout_secs=10 * 60)

    def network_named(self, network_name: str) -> str:
        return self.master.xe('network-list', {'name-label': network_name}, minimal=True)
