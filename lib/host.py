import json
import logging
import os
import shlex
import tempfile

from packaging import version

import lib.commands as commands

from lib.common import _param_get, safe_split, to_xapi_bool, wait_for, wait_for_not
from lib.common import prefix_object_name
from lib.sr import SR
from lib.vm import VM
from lib.xo import xo_cli, xo_object_exists

XAPI_CONF_FILE = '/etc/xapi.conf'
XAPI_CONF_DIR = '/etc/xapi.conf.d'

def host_data(hostname_or_ip):
    # read from data.py
    from data import HOST_DEFAULT_USER, HOST_DEFAULT_PASSWORD, HOSTS
    if hostname_or_ip in HOSTS:
        h_data = HOSTS[hostname_or_ip]
        return h_data
    else:
        return {'user': HOST_DEFAULT_USER, 'password': HOST_DEFAULT_PASSWORD}

class Host:
    xe_prefix = "host"

    def __init__(self, pool, hostname_or_ip):
        self.pool = pool
        self.hostname_or_ip = hostname_or_ip
        self.inventory = None
        self.uuid = None
        self.xo_srv_id = None

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

    def __str__(self):
        return self.hostname_or_ip

    def ssh(self, cmd, check=True, simple_output=True, suppress_fingerprint_warnings=True,
            background=False, decode=True):
        return commands.ssh(self.hostname_or_ip, cmd, check=check, simple_output=simple_output,
                            suppress_fingerprint_warnings=suppress_fingerprint_warnings, background=background,
                            decode=decode)

    def ssh_with_result(self, cmd):
        # doesn't raise if the command's return is nonzero, unless there's a SSH error
        return self.ssh(cmd, check=False, simple_output=False)

    def scp(self, src, dest, check=True, suppress_fingerprint_warnings=True, local_dest=False):
        return commands.scp(
            self.hostname_or_ip, src, dest, check=check,
            suppress_fingerprint_warnings=suppress_fingerprint_warnings, local_dest=local_dest
        )

    def xe(self, action, args={}, check=True, simple_output=True, minimal=False, force=False):
        maybe_param_minimal = ['--minimal'] if minimal else []
        maybe_param_force = ['--force'] if force else []

        def stringify(key, value):
            if isinstance(value, bool):
                return "{}={}".format(key, to_xapi_bool(value))
            return "{}={}".format(key, shlex.quote(value))

        command = ['xe', action] + maybe_param_minimal + maybe_param_force + \
                  [stringify(key, value) for key, value in args.items()]
        result = self.ssh(
            command,
            check=check,
            simple_output=simple_output
        )

        if result == 'true':
            return True
        if result == 'false':
            return False
        return result

    def param_get(self, param_name, key=None, accept_unknown_key=False):
        return _param_get(self, Host.xe_prefix, self.uuid, param_name, key, accept_unknown_key)

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

    def _get_xensource_inventory(self):
        output = self.ssh(['cat', '/etc/xensource-inventory'])
        inventory = {}
        for line in output.splitlines():
            key, raw_value = line.split('=')
            inventory[key] = raw_value.strip('\'')
        return inventory

    def xo_get_server_id(self, store=True):
        servers = xo_cli('server.getAll', use_json=True)
        for server in servers:
            if server['host'] == self.hostname_or_ip:
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
                if server['host'] == self.hostname_or_ip:
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
                'host': self.hostname_or_ip,
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
            if server['host'] == self.hostname_or_ip:
                return server['status']
        return None

    def xo_server_connected(self):
        return self.xo_server_status() == "connected"

    def xo_server_reconnect(self):
        logging.info("Reconnect XO to host %s" % self)
        xo_cli('server.disable', {'id': self.xo_srv_id})
        xo_cli('server.enable', {'id': self.xo_srv_id})
        wait_for(self.xo_server_connected, timeout_secs=10)
        # wait for XO to know about the host. Apparently a connected server status
        # is not enough to guarantee that the host object exists yet.
        wait_for(lambda: xo_object_exists(self.uuid), "Wait for XO to know about HOST %s" % self.uuid)

    def import_vm(self, uri, sr_uuid=None):
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
        logging.info("VM UUID: %s" % vm_uuid)
        vm_name = prefix_object_name(self.xe('vm-param-get', {'uuid': vm_uuid, 'param-name': 'name-label'}))
        self.xe('vm-param-set', {'uuid': vm_uuid, 'name-label': vm_name})
        vm = VM(vm_uuid, self)
        # Set VM VIF networks to the host's management network
        for vif in vm.vifs():
            vif.move(self.management_network())
        return vm

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
            wait_for(self.is_enabled, "Wait for host enabled", timeout_secs=1800)

    def is_enabled(self):
        try:
            return self.param_get('enabled')
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
        except commands.SSHCommandFailed as e:
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
        try:
            self.ssh(['yum', 'list', 'installed', package])
            return True
        except commands.SSHCommandFailed as e:
            if e.stdout.endswith('Error: No matching Packages to list'):
                return False
            raise e

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

    def reboot(self, verify=False, reconnect_xo=True):
        logging.info("Reboot host %s" % self)
        try:
            self.ssh(['reboot'])
        except commands.SSHCommandFailed as e:
            # ssh connection may get killed by the reboot and terminate with an error code
            if "closed by remote host" not in e.stdout:
                raise
        if verify or reconnect_xo:
            wait_for_not(self.is_enabled, "Wait for host down")
            wait_for(self.is_enabled, "Wait for host up", timeout_secs=1800)
        if reconnect_xo and self.is_master():
            self.xo_server_reconnect()

    def management_network(self):
        return self.xe('network-list', {'bridge': self.inventory['MANAGEMENT_INTERFACE']}, minimal=True)

    def disks(self):
        """ List of SCSI disks, e.g ['sda', 'sdb', 'nvme0n1']. """
        disks = self.ssh(['lsblk', '-nd', '-I', '8,259', '--output', 'NAME']).splitlines()
        disks.sort()
        return disks

    def disk_is_available(self, disk):
        return len(self.ssh(['lsblk', '-n', '-o', 'MOUNTPOINT', '/dev/' + disk]).strip()) == 0

    def available_disks(self):
        """
        Return a list of available disks for formatting, creating SRs or such.

        Returns a list of disk names (eg.: ['sdb', 'sdc']) that don't have any mountpoint in
        the output of lsblk (including their children such as partitions or md RAID devices)
        """
        return [disk for disk in self.disks() if self.disk_is_available(disk)]

    def file_exists(self, filepath, regular_file=True):
        option = '-f' if regular_file else '-e'
        return self.ssh_with_result(['test', option, filepath]).returncode == 0

    def binary_exists(self, binary):
        return self.ssh_with_result(['which', binary]).returncode == 0

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
        # Do not use `self.is_enabled` since it'd ask the XAPi of hostB1 before the join...
        wait_for(
            lambda: master.xe('host-param-get', {'uuid': self.uuid, 'param-name': 'enabled'}),
            f"Wait for pool {master} to see joined host {self} as enabled."
        )

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
