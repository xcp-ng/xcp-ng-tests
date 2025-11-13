from __future__ import annotations

import logging
import os
import re
import shlex
import tempfile
import uuid

from packaging import version

import lib.commands as commands
import lib.pif as pif

from typing import TYPE_CHECKING, Dict, List, Literal, Optional, TypedDict, Union, overload

if TYPE_CHECKING:
    from lib.pool import Pool

from lib.common import (
    DiskDevName,
    _param_add,
    _param_clear,
    _param_get,
    _param_remove,
    _param_set,
    prefix_object_name,
    safe_split,
    strip_suffix,
    strtobool,
    to_xapi_bool,
    wait_for,
    wait_for_not,
)
from lib.netutil import wait_for_ssh, wrap_ip
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM
from lib.xo import xo_cli, xo_object_exists

XAPI_CONF_FILE = '/etc/xapi.conf'
XAPI_CONF_DIR = '/etc/xapi.conf.d'

def host_data(hostname_or_ip):
    # read from data.py
    from data import HOST_DEFAULT_PASSWORD, HOST_DEFAULT_USER, HOSTS
    if hostname_or_ip in HOSTS:
        h_data = HOSTS[hostname_or_ip]
        return h_data
    else:
        return {'user': HOST_DEFAULT_USER, 'password': HOST_DEFAULT_PASSWORD}

class Host:
    xe_prefix = "host"
    pool: Pool

    # Data extraction is automatic, no conversion from str is done.
    BlockDeviceInfo = TypedDict('BlockDeviceInfo', {"name": str,
                                                    "kname": str,
                                                    "pkname": str,
                                                    "size": str,
                                                    "log-sec": str,
                                                    "type": str,
                                                    })
    BLOCK_DEVICES_FIELDS = ','.join(k.upper() for k in BlockDeviceInfo.__annotations__)

    block_devices_info: list[BlockDeviceInfo]

    def __init__(self, pool: Pool, hostname_or_ip):
        self.pool = pool
        self.hostname_or_ip = hostname_or_ip
        self.xo_srv_id: Optional[str] = None

        h_data = host_data(self.hostname_or_ip)
        self.user = h_data['user']
        self.password = h_data['password']
        self.skip_xo_config = h_data.get('skip_xo_config', False)

        self.saved_packages_list = None
        self.saved_rollback_id = None
        self.inventory = self._get_xensource_inventory()
        self.uuid = self.inventory['INSTALLATION_UUID']
        self.xcp_version = version.parse(self.inventory['PRODUCT_VERSION'])
        self.xcp_version_short = f"{self.xcp_version.major}.{self.xcp_version.minor}"
        self._dom0: Optional[VM] = None

        self.rescan_block_devices_info()

    def __str__(self):
        return self.hostname_or_ip

    @overload
    def ssh(self, cmd: Union[str, List[str]], *, check: bool = True, simple_output: Literal[True] = True,
            suppress_fingerprint_warnings: bool = True, background: Literal[False] = False,
            decode: Literal[True] = True) -> str:
        ...

    @overload
    def ssh(self, cmd: Union[str, List[str]], *, check: bool = True, simple_output: Literal[True] = True,
            suppress_fingerprint_warnings: bool = True, background: Literal[False] = False,
            decode: Literal[False]) -> bytes:
        ...

    @overload
    def ssh(self, cmd: Union[str, List[str]], *, check: bool = True, simple_output: Literal[False],
            suppress_fingerprint_warnings: bool = True, background: Literal[False] = False,
            decode: bool = True) -> commands.SSHResult:
        ...

    @overload
    def ssh(self, cmd: Union[str, List[str]], *, check: bool = True, simple_output: bool = True,
            suppress_fingerprint_warnings: bool = True, background: Literal[True],
            decode: bool = True) -> None:
        ...

    @overload
    def ssh(self, cmd: Union[str, List[str]], *, check: bool = True, simple_output: bool = True,
            suppress_fingerprint_warnings: bool = True, background: bool = False, decode: bool = True) \
            -> Union[str, bytes, commands.SSHResult, None]:
        ...

    def ssh(self, cmd, *, check=True, simple_output=True, suppress_fingerprint_warnings=True,
            background=False, decode=True):
        return commands.ssh(self.hostname_or_ip, cmd, check=check, simple_output=simple_output,
                            suppress_fingerprint_warnings=suppress_fingerprint_warnings,
                            background=background, decode=decode)

    def ssh_with_result(self, cmd) -> commands.SSHResult:
        # doesn't raise if the command's return is nonzero, unless there's a SSH error
        return commands.ssh_with_result(self.hostname_or_ip, cmd)

    def scp(self, src, dest, check=True, suppress_fingerprint_warnings=True, local_dest=False):
        return commands.scp(
            self.hostname_or_ip, src, dest, check=check,
            suppress_fingerprint_warnings=suppress_fingerprint_warnings, local_dest=local_dest
        )

    @overload
    def xe(self, action: str, args: Dict[str, Union[str, bool]] = {}, *, check: bool = ...,
           simple_output: Literal[True] = ..., minimal: bool = ..., force: bool = ...) -> str:
        ...

    @overload
    def xe(self, action: str, args: Dict[str, Union[str, bool]] = {}, *, check: bool = ...,
           simple_output: Literal[False], minimal: bool = ..., force: bool = ...) -> commands.SSHResult:
        ...

    def xe(self, action, args={}, *, check=True, simple_output=True, minimal=False, force=False) \
            -> Union[str, commands.SSHResult]:
        maybe_param_minimal = ['--minimal'] if minimal else []
        maybe_param_force = ['--force'] if force else []

        def stringify(key, value):
            if isinstance(value, bool):
                return "{}={}".format(key, to_xapi_bool(value))
            if isinstance(value, dict):
                ret = ""
                for key2, value2 in value.items():
                    ret += f"{key}:{key2}={value2} "
                return ret.rstrip()
            return "{}={}".format(key, shlex.quote(value))

        command: List[str] = ['xe', action] + maybe_param_minimal + maybe_param_force + \
                             [stringify(key, value) for key, value in args.items()]
        result = self.ssh(
            command,
            check=check,
            simple_output=simple_output
        )
        assert isinstance(result, (str, commands.SSHResult))

        return result

    @overload
    def param_get(self, param_name: str, key: Optional[str] = ...,
                  accept_unknown_key: Literal[False] = ...) -> str:
        ...

    @overload
    def param_get(self, param_name: str, key: Optional[str] = ...,
                  accept_unknown_key: Literal[True] = ...) -> Optional[str]:
        ...

    def param_get(self, param_name: str, key: Optional[str] = None, accept_unknown_key: bool = False) -> Optional[str]:
        return _param_get(self, self.xe_prefix, self.uuid,
                          param_name, key, accept_unknown_key)

    def param_set(self, param_name, value, key=None):
        _param_set(self, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_remove(self, param_name, key, accept_unknown_key=False):
        _param_remove(self, self.xe_prefix, self.uuid,
                      param_name, key, accept_unknown_key)

    def param_add(self, param_name, value, key=None):
        _param_add(self, self.xe_prefix, self.uuid,
                   param_name, value, key)

    def param_clear(self, param_name):
        _param_clear(self, self.xe_prefix, self.uuid,
                     param_name)

    def create_file(self, filename, text):
        with tempfile.NamedTemporaryFile('w') as file:
            file.write(text)
            file.flush()
            self.scp(file.name, filename)

    def add_xcpng_repo(self, name, base_repo='xcp-ng'):
        assert base_repo in ['xcp-ng', 'vates']
        base_repo_url = 'http://mirrors.xcp-ng.org/' if base_repo == 'xcp-ng' else 'https://repo.vates.tech/xcp-ng/'
        major = self.xcp_version.major
        version = self.xcp_version_short
        self.create_file(f"/etc/yum.repos.d/xcp-ng-{name}.repo", (
            f"[xcp-ng-{name}]\n"
            f"name=XCP-ng {name} Repository\n"
            f"baseurl={base_repo_url}/{major}/{version}/{name}/x86_64/\n"
            "enabled=1\n"
            "gpgcheck=1\n"
            "repo_gpgcheck=1\n"
            "gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-xcpng\n"
        ))

    def remove_xcpng_repo(self, name):
        self.ssh(['rm -f /etc/yum.repos.d/xcp-ng-{}.repo'.format(name)])

    def execute_script(self, script_contents, shebang='sh', simple_output=True):
        with tempfile.NamedTemporaryFile('w') as script:
            os.chmod(script.name, 0o775)
            script.write('#!/usr/bin/env ' + shebang + '\n')
            script.write(script_contents)
            script.flush()
            self.scp(script.name, script.name)

            try:
                logging.debug(f"[{self}] # Will execute this temporary script:\n{script_contents.strip()}")
                return self.ssh([script.name], simple_output=simple_output)
            finally:
                self.ssh(['rm', '-f', script.name])

    def _get_xensource_inventory(self) -> Dict[str, str]:
        output = self.ssh(['cat', '/etc/xensource-inventory'])
        inventory: Dict[str, str] = {}
        for line in output.splitlines():
            key, raw_value = line.split('=')
            inventory[key] = raw_value.strip('\'')
        return inventory

    def xo_get_server_id(self, store=True):
        servers = xo_cli('server.getAll', use_json=True)
        for server in servers:
            if server['host'] == wrap_ip(self.hostname_or_ip):
                if store:
                    self.xo_srv_id = server['id']
                return server['id']
        return None

    def xo_server_remove(self):
        if self.xo_srv_id is not None:
            xo_cli('server.remove', {'id': self.xo_srv_id})
        else:
            servers = xo_cli('server.getAll', use_json=True)
            for server in servers:
                if server['host'] == wrap_ip(self.hostname_or_ip):
                    xo_cli('server.remove', {'id': server['id']})

    def xo_server_add(self, username, password, label=None, unregister_first=True):
        """ Returns the server ID created by XO's `server.add`. """
        if unregister_first:
            self.xo_server_remove()
        if label is None:
            label = 'Auto tests %s' % self.hostname_or_ip
        xo_srv_id = xo_cli(
            'server.add',
            {
                'host': wrap_ip(self.hostname_or_ip),
                'username': username,
                'password': password,
                'allowUnauthorized': 'true',
                'label': label
            }
        )
        self.xo_srv_id = xo_srv_id

    def xo_server_status(self):
        servers = xo_cli('server.getAll', use_json=True)
        for server in servers:
            if server['host'] == wrap_ip(self.hostname_or_ip):
                return server['status']
        return None

    def xo_server_connected(self):
        return self.xo_server_status() == "connected"

    def xo_server_reconnect(self):
        assert self.xo_srv_id is not None
        logging.info("Reconnect XO to host %s" % self)
        xo_cli('server.disable', {'id': self.xo_srv_id})
        xo_cli('server.enable', {'id': self.xo_srv_id})
        wait_for(self.xo_server_connected, timeout_secs=10)
        # wait for XO to know about the host. Apparently a connected server status
        # is not enough to guarantee that the host object exists yet.
        wait_for(lambda: xo_object_exists(self.uuid), "Wait for XO to know about HOST %s" % self.uuid)

    @staticmethod
    def vm_cache_key(uri):
        return f"[Cache for {strip_suffix(uri, '.xva')}]"

    def cached_vm(self, uri, sr_uuid):
        assert sr_uuid, "A SR UUID is necessary to use import cache"
        cache_key = self.vm_cache_key(uri)
        # Look for an existing cache VM
        vm_uuids = safe_split(self.xe('vm-list', {'name-description': cache_key}, minimal=True), ',')

        for vm_uuid in vm_uuids:
            vm = VM(vm_uuid, self)
            # Make sure the VM is on the wanted SR.
            # Assumption: if the first disk is on the SR, the VM is.
            # If there's no VDI at all, then it is virtually on any SR.
            if not vm.vdi_uuids() or vm.get_sr().uuid == sr_uuid:
                logging.info(f"Reusing cached VM {vm.uuid} for {uri}")
                return vm
        logging.info("Could not find a VM in cache for %r", uri)

    def import_vm(self, uri, sr_uuid=None, use_cache=False):
        vm = None
        if use_cache:
            if '://' in uri and uri.startswith("clone"):
                protocol, rest = uri.split(":", 1)
                assert rest.startswith("//")
                filename = rest[2:] # strip "//"
                base_vm = self.cached_vm(filename, sr_uuid)
                if base_vm:
                    vm = base_vm.clone()
                    vm.param_clear('name-description')
                    if uri.startswith("clone+start"):
                        vm.start()
                        wait_for(vm.is_running, "Wait for VM running")
            else:
                vm = self.cached_vm(uri, sr_uuid)
            if vm:
                return vm
        else:
            assert not ('://' in uri and uri.startswith("clone")), "clone URIs require cache enabled"

        params = {}
        msg = "Import VM %s" % uri
        if '://' in uri:
            params['url'] = uri
        else:
            params['filename'] = uri
        if sr_uuid is not None:
            msg += " (SR: %s)" % sr_uuid
            params['sr-uuid'] = sr_uuid
        logging.info(msg)
        vm_uuid = self.xe('vm-import', params)
        vm_name = prefix_object_name(self.xe('vm-param-get', {'uuid': vm_uuid, 'param-name': 'name-label'}))
        vm = VM(vm_uuid, self)
        vm.param_set('name-label', vm_name)
        # Set VM VIF networks to the host's management network
        for vif in vm.vifs():
            vif.move(self.management_network())
        if use_cache:
            cache_key = self.vm_cache_key(uri)
            logging.info(f"Marking VM {vm.uuid} as cached")
            vm.param_set('name-description', cache_key)
        return vm

    def import_iso(self, uri, sr: SR):
        random_name = str(uuid.uuid4())

        vdi_uuid = self.xe(
            "vdi-create",
            {
                "sr-uuid": sr.uuid,
                "name-label": random_name,
                "virtual-size": "0",
            },
        )

        download_path = None
        try:
            params: Dict[str, Union[str, bool]] = {'uuid': vdi_uuid}
            if '://' in uri:
                logging.info(f"Download ISO {uri}")
                download_path = f'/tmp/{vdi_uuid}'
                self.ssh(f"curl -o '{download_path}' '{uri}'")
                params['filename'] = download_path
            else:
                params['filename'] = uri
            logging.info(f"Import ISO {uri}: name {random_name}, uuid {vdi_uuid}")

            self.xe('vdi-import', params)
        finally:
            if download_path:
                self.ssh(f"rm -f '{download_path}'")

        return VDI(vdi_uuid, sr=sr)

    def vm_from_template(self, name, template):
        params = {
            "new-name-label": prefix_object_name(name),
            "template": template,
            "sr-uuid": self.main_sr_uuid(),
        }
        vm_uuid = self.xe('vm-install', params)
        return VM(vm_uuid, self)

    def pool_has_vm(self, vm_uuid, vm_type='vm'):
        if vm_type == 'snapshot':
            return self.xe('snapshot-list', {'uuid': vm_uuid}, minimal=True) == vm_uuid
        else:
            return self.xe('vm-list', {'uuid': vm_uuid}, minimal=True) == vm_uuid

    def install_updates(self):
        logging.info("Install updates on host %s" % self)
        return self.ssh(['yum', 'update', '-y'])

    def restart_toolstack(self, verify=False):
        logging.info("Restart toolstack on host %s" % self)
        self.ssh(['xe-toolstack-restart'])
        if verify:
            wait_for(self.is_enabled, "Wait for host enabled", timeout_secs=30 * 60)

    def is_enabled(self) -> bool:
        try:
            return strtobool(self.param_get('enabled'))
        except commands.SSHCommandFailed:
            # If XAPI is not ready yet, or the host is down, this will throw. We return False in that case.
            return False

    def has_updates(self):
        try:
            # yum check-update returns 100 if there are updates, 1 if there's an error, 0 if no updates
            self.ssh(['yum', 'check-update'])
            # returned 0, else there would have been a SSHCommandFailed
            return False
        except commands.SSHCommandFailed as e:
            if e.returncode == 100:
                return True
            else:
                raise

    def get_last_yum_history_tid(self):
        """
        Get the last transaction in yum history.

        The output looks like this (when not polluted by plugin output, hence '--noplugins' below):

        ID     | Command line             | Date and time    | Action(s)      | Altered
        -------------------------------------------------------------------------------
            37 | install -y --enablerepo= | 2021-03-08 15:27 | Install        |    1
            36 | remove ceph-common       | 2021-03-08 15:26 | Erase          |    1
            35 | install -y --enablerepo= | 2021-03-08 15:19 | Install        |    1
            34 | remove -y ceph-common    | 2021-03-08 15:13 | Erase          |    1
        [...]
        """
        try:
            history_str = self.ssh(['yum', 'history', 'list', '--noplugins'])
        except commands.SSHCommandFailed:
            # yum history list fails if the list is empty, and it's also not possible to rollback
            # to before the first transaction, so "0" would not be appropriate as last transaction.
            # To workaround this, create transactions: install and remove a small package.
            logging.info('Install and remove a small package to workaround empty yum history.')
            self.yum_install(['gpm-libs'])
            self.yum_remove(['gpm-libs'])
            history_str = self.ssh(['yum', 'history', 'list', '--noplugins'])

        history = history_str.splitlines()
        line_index = None
        for i in range(len(history)):
            if history[i].startswith('--------'):
                line_index = i
                break

        if line_index is None:
            raise Exception('Unable to get yum transactions')

        try:
            return int(history[line_index + 1].split()[0])
        except ValueError:
            raise Exception('Unable to parse correctly last yum history tid. Output:\n' + history_str)

    def yum_install(self, packages, enablerepo=None):
        logging.info('Install packages: %s on host %s' % (' '.join(packages), self))
        enablerepo_cmd = ['--enablerepo=%s' % enablerepo] if enablerepo is not None else []
        return self.ssh(['yum', 'install', '--setopt=skip_missing_names_on_install=False', '-y']
                        + enablerepo_cmd + packages)

    def yum_remove(self, packages):
        logging.info('Remove packages: %s from host %s' % (' '.join(packages), self))
        return self.ssh(['yum', 'remove', '-y'] + packages)

    def packages(self):
        """ Returns the list of installed RPMs - with version, release, arch and epoch. """
        return sorted(
            self.ssh(['rpm', '-qa', '--qf', '%{NAME}-%{VERSION}-%{RELEASE}-%{ARCH}-%{EPOCH}\\\\n']).splitlines()
        )

    def check_packages_available(self, packages):
        """ Check if a given package list is available in the YUM repositories. """
        return len(self.ssh(['repoquery'] + packages).splitlines()) == len(packages)

    def get_available_package_versions(self, package):
        return self.ssh(['repoquery', '--show-duplicates', package]).splitlines()

    def is_package_installed(self, package):
        return self.ssh_with_result(['rpm', '-q', package]).returncode == 0

    def yum_save_state(self):
        logging.info(f"Save yum state for host {self}")
        # For now, that saved state feature does not support several saved states
        assert self.saved_packages_list is None, "There is already a saved package list set"
        self.saved_packages_list = self.packages()
        self.saved_rollback_id = self.get_last_yum_history_tid()

    def yum_restore_saved_state(self):
        logging.info(f"Restore yum state for host {self}")
        """ Restore yum state to saved state. """
        assert self.saved_packages_list is not None, \
            "Can't restore previous state without a package list: no saved packages list"
        assert self.saved_rollback_id is not None, \
            "Can't restore previous state without a package list: no rollback id"

        assert isinstance(self.saved_rollback_id, int)

        self.ssh([
            'yum', 'history', 'rollback', '--enablerepo=xcp-ng-base,xcp-ng-testing,xcp-ng-updates',
            str(self.saved_rollback_id), '-y'
        ])
        pkgs = self.packages()
        if self.saved_packages_list != pkgs:
            missing = [x for x in self.saved_packages_list if x not in set(pkgs)]
            extra = [x for x in pkgs if x not in set(self.saved_packages_list) and not x.startswith("gpg-pubkey-")]
            if missing or extra:
                raise Exception(
                    "Yum state badly restored. Missing: [%s], extra: [%s]." % (' '.join(missing), ' '.join(extra))
                )
        # We can resave a new state after that.
        self.saved_packages_list = None
        self.saved_rollback_id = None

    def reboot(self, verify=False):
        logging.info("Reboot host %s" % self)
        try:
            self.ssh(['reboot'])
        except commands.SSHCommandFailed as e:
            # ssh connection may get killed by the reboot and terminate with an error code
            if "closed by remote host" not in e.stdout:
                raise
        if verify:
            wait_for_not(self.is_enabled, "Wait for host down")
            wait_for_ssh(self.hostname_or_ip)
            wait_for(self.is_enabled, "Wait for XAPI to be ready", timeout_secs=30 * 60)

    def management_network(self):
        return self.xe('network-list', {'bridge': self.inventory['MANAGEMENT_INTERFACE']}, minimal=True)

    def management_pif(self):
        uuid = self.xe('pif-list', {'management': True, 'host-uuid': self.uuid}, minimal=True)
        return pif.PIF(uuid, self)

    def rescan_block_devices_info(self) -> None:
        """
        Initalize static informations about the disks.

        Despite those being static, it can be necessary to rescan,
        when we test how XCP-ng reacts to changes of hardware (or
        reconfiguration of device blocksize), or after a reboot.
        """
        output_string = self.ssh(["lsblk", "--pairs", "--bytes",
                                  '-I', '8,259', # limit to: sd, blkext
                                  "--output", Host.BLOCK_DEVICES_FIELDS])

        self.block_devices_info = [
            Host.BlockDeviceInfo({key.lower(): value.strip('"') # type: ignore[misc]
                                  for key, value in re.findall(r'(\S+)=(".*?"|\S+)', line)})
            for line in output_string.strip().splitlines()
        ]
        logging.debug("blockdevs found: %s", [disk["name"] for disk in self.block_devices_info])

    def disks(self) -> list[Host.BlockDeviceInfo]:
        """ List of BlockDeviceInfo for all disks. """
        # filter out partitions from block_devices
        return sorted((disk for disk in self.block_devices_info if not disk["pkname"]),
                      key=lambda disk: disk["name"])

    def disk_is_available(self, disk: DiskDevName) -> bool:
        """
        Check if a disk is unmounted and appears available for use.

        It may or may not contain identifiable filesystem or partition label.
        If there are no mountpoints, it is assumed that the disk is not in use.

        Warn: This function may misclassify LVM_member disks (e.g. in XOSTOR, RAID, ZFS) as "available".
        Such disks may not have mountpoints but still be in use.
        """
        return len(self.ssh(['lsblk', '--noheadings', '-o', 'MOUNTPOINT', '/dev/' + disk]).strip()) == 0

    def file_exists(self, filepath, regular_file=True):
        option = '-f' if regular_file else '-e'
        return self.ssh_with_result(['test', option, filepath]).returncode == 0

    def binary_exists(self, binary):
        return self.ssh_with_result(['which', binary]).returncode == 0

    def is_symlink(self, filepath):
        return self.ssh_with_result(['test', '-L', filepath]).returncode == 0

    def sr_create(self, sr_type, label, device_config, shared=False, verify=False):
        params = {
            'host-uuid': self.uuid,
            'type': sr_type,
            'name-label': prefix_object_name(label),
            'content-type': 'iso' if sr_type == 'iso' else 'user',
            'shared': shared
        }
        for key, value in device_config.items():
            params['device-config:{}'.format(key)] = value

        logging.info(
            f"Create {sr_type} SR on host {self} with label '{label}' and device-config: {str(device_config)}"
        )
        sr_uuid = self.xe('sr-create', params)
        sr = SR(sr_uuid, self.pool)
        if verify:
            wait_for(sr.exists, "Wait for SR to exist")
        return sr

    def is_master(self):
        return self.ssh(['cat', '/etc/xensource/pool.conf']) == 'master'

    def local_vm_srs(self):
        srs = []
        sr_uuids = safe_split(self.xe('pbd-list', {'host-uuid': self.uuid, 'params': 'sr-uuid'}, minimal=True))
        for sr_uuid in sr_uuids:
            sr = SR(sr_uuid, self.pool)
            if sr.content_type() == 'user' and not sr.is_shared():
                srs.append(sr)
        return srs

    def main_sr_uuid(self):
        """ Main SR is the default SR, the first local SR, or a specific SR depending on data.py's DEFAULT_SR. """
        try:
            from data import DEFAULT_SR
        except ImportError:
            DEFAULT_SR = 'default'

        sr_uuid = None
        if DEFAULT_SR == 'local':
            hostname = self.xe('host-param-get', {'uuid': self.uuid,
                                                  'param-name': 'name-label'})
            local_sr_uuids = safe_split(
                # xe sr-list doesn't support filtering by host UUID!
                self.xe('sr-list', {'host': hostname, 'content-type': 'user', 'minimal': 'true'}),
                ','
            )
            assert local_sr_uuids, f"DEFAULT_SR=='local' so there must be a local SR on host {self}"
            sr_uuid = local_sr_uuids[0]
        elif DEFAULT_SR == 'default':
            sr_uuid = self.pool.param_get('default-SR')
            assert sr_uuid, f"DEFAULT_SR='default' so there must be a default SR on the pool of host {self}"
        else:
            sr_uuid = DEFAULT_SR
            assert self.xe('sr-list', {'uuid': sr_uuid}), f"cannot find SR with UUID {sr_uuid} on host {self}"
        assert sr_uuid != "<not in database>"
        return sr_uuid

    def hostname(self):
        return self.ssh(['hostname'])

    def call_plugin(self, plugin_name, function, args=None):
        params = {'host-uuid': self.uuid, 'plugin': plugin_name, 'fn': function}
        if args is not None:
            for k, v in args.items():
                params['args:%s' % k] = v
        return self.xe('host-call-plugin', params)

    def join_pool(self, pool):
        master = pool.master
        self.xe('pool-join', {
            'master-address': master.hostname_or_ip,
            'master-username': master.user,
            'master-password': master.password
        })
        wait_for(
            lambda: self.uuid in pool.hosts_uuids(),
            f"Wait for joining host {self} to appear in joined pool {master}."
        )
        pool.hosts.append(Host(pool, pool.host_ip(self.uuid)))
        # Do not use `self.is_enabled` since it'd ask the XAPI of hostB1 before the join...
        wait_for(
            lambda: strtobool(master.xe('host-param-get', {'uuid': self.uuid, 'param-name': 'enabled'})),
            f"Wait for pool {master} to see joined host {self} as enabled."
        )
        self.pool = pool

    def activate_smapi_driver(self, driver):
        sm_plugins = self.ssh(['grep', '[[:space:]]*sm-plugins[[:space:]]*=[[:space:]]*', XAPI_CONF_FILE]).splitlines()
        sm_plugins = sm_plugins[-1] + ' ' + driver
        self.ssh([f'echo "{sm_plugins}" > {XAPI_CONF_DIR}/00-XCP-ng-tests-sm-driver-{driver}.conf'])
        self.restart_toolstack(verify=True)

    def deactivate_smapi_driver(self, driver):
        self.ssh(['rm', '-f', f'{XAPI_CONF_DIR}/00-XCP-ng-tests-sm-driver-{driver}.conf'])
        self.restart_toolstack(verify=True)

    def varstore_dir(self):
        if self.xcp_version < version.parse("8.3"):
            return "/var/lib/uefistored"
        else:
            return "/var/lib/varstored"

    def enable_hsts_header(self):
        self.ssh(['echo', '"hsts_max_age = 63072000"', '>',
                  f'{XAPI_CONF_DIR}/00-XCP-ng-tests-enable-hsts-header.conf'])
        self.restart_toolstack(verify=True)

    def disable_hsts_header(self):
        self.ssh(['rm', '-f', f'{XAPI_CONF_DIR}/00-XCP-ng-tests-enable-hsts-header.conf'])
        self.restart_toolstack(verify=True)

    def get_dom0_uuid(self):
        return self.inventory["CONTROL_DOMAIN_UUID"]

    def get_dom0_vm(self) -> VM:
        if not self._dom0:
            self._dom0 = VM(self.get_dom0_uuid(), self)
        return self._dom0

    def get_sr_from_vdi_uuid(self, vdi_uuid: str) -> Optional[SR]:
        sr_uuid = self.xe("vdi-param-get", {
            "param-name": "sr-uuid",
            "uuid": vdi_uuid,
        })
        if not sr_uuid:
            return None
        return SR(sr_uuid, self.pool)

    def lvs(self, vgName: Optional[str] = None, ignore_MGT: bool = True) -> List[str]:
        ret: List[str] = []
        cmd = ["lvs", "--noheadings", "-o", "LV_NAME"]
        if vgName:
            cmd.append(vgName)
        output = self.ssh(cmd)
        for line in output.splitlines():
            if ignore_MGT and "MGT" in line:
                continue
            ret.append(line.strip())
        return ret
