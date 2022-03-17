import json
import logging
import os
import shlex
import tempfile

import lib.commands as commands

from lib.common import to_xapi_bool, wait_for, wait_for_not
from lib.sr import SR
from lib.vm import VM
from lib.xo import xo_cli, xo_object_exists

class Host:
    def __init__(self, pool, hostname_or_ip):
        self.pool = pool
        self.hostname_or_ip = hostname_or_ip
        self.inventory = None
        self.uuid = None
        self.xo_srv_id = None
        self.user = None
        self.password = None
        self.saved_packages_list = None
        self.saved_rollback_id = None
        self.inventory = self._get_xensource_inventory()
        self.uuid = self.inventory['INSTALLATION_UUID']

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

    def xe(self, action, args={}, check=True, simple_output=True, minimal=False, use_scp=False):
        maybe_param_minimal = ['--minimal'] if minimal else []

        def stringify(key, value):
            if type(value) == bool:
                return "{}={}".format(key, to_xapi_bool(value))
            return "{}={}".format(key, shlex.quote(value))

        command = ['xe', action] + maybe_param_minimal + [stringify(key, value) for key, value in args.items()]
        if use_scp:
            result = self.execute_script(' '.join(command), 'sh', simple_output)
        else:
            result = self.ssh(
                [shlex.quote(' '.join(command))],
                check=check,
                simple_output=simple_output
            )

        if result == 'true':
            return True
        if result == 'false':
            return False
        return result

    def execute_script(self, script_contents, shebang='sh', simple_output=True):
        with tempfile.NamedTemporaryFile('w') as script:
            os.chmod(script.name, 0o775)
            script.write('#!/usr/bin/env ' + shebang + '\n')
            script.write(script_contents)
            script.flush()
            self.scp(script.name, script.name)

            try:
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
        servers = json.loads(xo_cli('server.getAll'))
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
            servers = json.loads(xo_cli('server.getAll'))
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
        servers = json.loads(xo_cli('server.getAll'))
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
        return self.ssh(['xe-toolstack-restart'])
        if verify:
            wait_for(self.is_enabled, "Wait for host enabled")

    def is_enabled(self):
        try:
            return self.xe('host-param-get', {'uuid': self.uuid, 'param-name': 'enabled'})
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

        The output looks like this:
        Loaded plugins: fastestmirror
        ID     | Command line             | Date and time    | Action(s)      | Altered
        -------------------------------------------------------------------------------
            37 | install -y --enablerepo= | 2021-03-08 15:27 | Install        |    1
            36 | remove ceph-common       | 2021-03-08 15:26 | Erase          |    1
            35 | install -y --enablerepo= | 2021-03-08 15:19 | Install        |    1
            34 | remove -y ceph-common    | 2021-03-08 15:13 | Erase          |    1
        """
        history = self.ssh(['yum', 'history', 'list']).splitlines()
        return history[3].split()[0]

    def yum_install(self, packages, enablerepo=None):
        logging.info('Install packages: %s on host %s' % (' '.join(packages), self))
        enablerepo_cmd = ['--enablerepo=%s' % enablerepo] if enablerepo is not None else []
        return self.ssh(['yum', 'install', '-y'] + enablerepo_cmd + packages)

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

    def yum_save_state(self):
        # For now, that saved state feature does not support several saved states
        assert self.saved_packages_list is None, "There is already a saved package list set"
        self.saved_packages_list = self.packages()
        self.saved_rollback_id = self.get_last_yum_history_tid()

    def yum_restore_saved_state(self):
        """ Restore yum state to saved state. """
        assert self.saved_packages_list is not None, \
            "Can't restore previous state without a package list: no saved packages list"
        assert self.saved_rollback_id is not None, \
            "Can't restore previous state without a package list: no rollback id"
        self.ssh([
            'yum', 'history', 'rollback', '--enablerepo=xcp-ng-base,xcp-ng-testing,xcp-ng-updates',
            self.saved_rollback_id, '-y'
        ])
        pkgs = self.packages()
        if self.saved_packages_list != pkgs:
            missing = [x for x in self.saved_packages_list if x not in set(pkgs)]
            extra = [x for x in pkgs if x not in set(self.saved_packages_list)]
            raise Exception(
                "Yum state badly restored missing: [%s], extra: [%s]." % (' '.join(missing), ' '.join(extra))
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
            wait_for(self.is_enabled, "Wait for host up", timeout_secs=600)
        if reconnect_xo and self.is_master():
            self.xo_server_reconnect()

    def management_network(self):
        return self.xe('network-list', {'bridge': self.inventory['MANAGEMENT_INTERFACE']}, minimal=True)

    def disks(self):
        """ List of SCSI disks, e.g ['sda', 'sdb']. """
        disks = self.ssh(['lsblk', '-nd', '-I', '8', '--output', 'NAME']).splitlines()
        disks.sort()
        return disks

    def file_exists(self, filepath, regular_file=True):
        option = '-f' if regular_file else '-e'
        return self.ssh_with_result(['test', option, filepath]).returncode == 0

    def binary_exists(self, binary):
        return self.ssh_with_result(['which', binary]).returncode == 0

    def sr_create(self, sr_type, label, device_config, shared=False, verify=False):
        params = {
            'host-uuid': self.uuid,
            'type': sr_type,
            'name-label': label,
            'content-type': 'user',
            'shared': shared
        }
        for key, value in device_config.items():
            params['device-config:{}'.format(key)] = value

        logging.info(
            "Create %s SR on host %s's %s device-config with label '%s'" %
            (sr_type, self, str(device_config), label)
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
        for sr_uuid in self.xe('pbd-list', {'host-uuid': self.uuid, 'params': 'sr-uuid'}, minimal=True).split(','):
            sr = SR(sr_uuid, self.pool)
            if sr.content_type() == 'user' and not sr.is_shared():
                srs.append(sr)
        return srs

    def hostname(self):
        return self.ssh(['hostname'])

    def call_plugin(self, plugin_name, function, args=None, use_scp=False):
        params = {'host-uuid': self.uuid, 'plugin': plugin_name, 'fn': function}
        if args is not None:
            for k, v in args.items():
                params['args:%s' % k] = v
        return self.xe('host-call-plugin', params, use_scp=use_scp)
