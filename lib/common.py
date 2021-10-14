import json
import logging
import os
import shlex
import subprocess
import tempfile
import time
from enum import Enum
from uuid import UUID

import lib.config as config
from lib.efi import EFIAuth, EFI_HEADER_MAGIC

class PackageManagerEnum(Enum):
    UNKNOWN = 1
    RPM = 2
    APT_GET = 3

# Common VM images used in tests
def vm_image(vm_key):
    from data import VM_IMAGES, DEF_VM_URL
    url = VM_IMAGES[vm_key]
    if not url.startswith('http'):
        url = DEF_VM_URL + url
    return url

def wait_for(fn, msg=None, timeout_secs=120, retry_delay_secs=2, invert=False):
    if msg is not None:
        logging.info(msg)
    time_left = timeout_secs
    while True:
        ret = fn()
        if not invert and ret:
            return
        if invert and not ret:
            return
        time_left -= retry_delay_secs
        if time_left <= 0:
            expected = 'True' if not invert else 'False'
            raise Exception("Timeout reached while waiting for fn call to yield %s (%s)." % (expected, timeout_secs))
        time.sleep(retry_delay_secs)

def wait_for_not(*args, **kwargs):
    return wait_for(*args, **kwargs, invert=True)

def is_uuid(maybe_uuid):
    try:
        UUID(maybe_uuid, version=4)
        return True
    except ValueError:
        return False

def xo_cli(action, args={}, check=True, simple_output=True):
    res = subprocess.run(
        ['xo-cli', action] + ["%s=%s" % (key, value) for key, value in args.items()],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check
    )
    if simple_output:
        return res.stdout.decode().strip()
    else:
        return res

def xo_object_exists(uuid):
    lst = json.loads(xo_cli('--list-objects', {'uuid': uuid}))
    return len(lst) > 0

class SSHCommandFailed(Exception):
    __slots__ = 'returncode', 'stdout', 'cmd'

    def __init__(self, returncode, stdout, cmd):
        super(SSHCommandFailed, self).__init__(
            'SSH command ({}) failed with return code {}: {}'.format(cmd, returncode, stdout)
        )
        self.returncode = returncode
        self.stdout = stdout
        self.cmd = cmd

class SSHResult:
    __slots__ = 'returncode', 'stdout'

    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout

def _ellide_log_lines(log):
    if log == '':
        return log

    if config.ssh_output_max_lines < 1:
        return "\n{}".format(log)

    reduced_message = log.split("\n")
    if len(reduced_message) > config.ssh_output_max_lines:
        reduced_message = reduced_message[:config.ssh_output_max_lines - 1]
        reduced_message.append("(...)")
    return "\n{}".format("\n".join(reduced_message))

def ssh(hostname_or_ip, cmd, check=True, simple_output=True, suppress_fingerprint_warnings=True,
        background=False, target_os='linux', decode=True):
    options = []
    if suppress_fingerprint_warnings:
        # Suppress warnings and questions related to host key fingerprints
        # because on a test network IPs get reused, VMs are reinstalled, etc.
        # Based on https://unix.stackexchange.com/a/365976/257493
        options.append('-o "StrictHostKeyChecking no"')
        options.append('-o "LogLevel ERROR"')
        options.append('-o "UserKnownHostsFile /dev/null"')

    command = " ".join(cmd)
    if background and target_os != "windows":
        # https://stackoverflow.com/questions/29142/getting-ssh-to-execute-a-command-in-the-background-on-target-machine
        command = "nohup %s &>/dev/null &" % command

    if background and target_os == "windows":
        # Unfortunately the "nohup" solution doesn't always work well with windows+openssh+git-bash
        # Sometimes commands that end in '&' are not executed at all
        # So we spawn the ssh process in the background
        return subprocess.Popen(
            "ssh root@%s %s '%s'" % (hostname_or_ip, ' '.join(options), command),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
    else:
        # Common case

        # Fetch banner and remove it to avoid stdout/stderr pollution.
        if config.ignore_ssh_banner:
            banner_res = subprocess.run(
                "ssh root@%s %s '%s'" % (hostname_or_ip, ' '.join(options), '\n'),
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False
            )
        res = subprocess.run(
            "ssh root@%s %s '%s'" % (hostname_or_ip, ' '.join(options), command),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False
        )

        # get a decoded version of the output in any case, replacing potential errors
        output_for_logs = res.stdout.decode(errors='replace').strip()

        # Even if check is False, we still raise in case of return code 255, which means a SSH error.
        if res.returncode == 255:
            raise SSHCommandFailed(255, "SSH Error: %s" % output_for_logs, command)

        output = res.stdout
        if config.ignore_ssh_banner:
            if banner_res.returncode == 255:
                raise SSHCommandFailed(255, "SSH Error: %s" % banner_res.stdout.decode(errors='replace'), command)
            output = output[len(banner_res.stdout):]

        if decode:
            output = output.decode()

        errorcode_msg = "" if res.returncode == 0 else " - Got error code: %s" % res.returncode
        logging.debug(f"[{hostname_or_ip}] {command}{errorcode_msg}{_ellide_log_lines(output_for_logs)}")

        if res.returncode and check:
            raise SSHCommandFailed(res.returncode, output_for_logs, command)

        if simple_output:
            return output.strip()
        return SSHResult(res.returncode, output)

def scp(hostname_or_ip, src, dest, check=True, suppress_fingerprint_warnings=True, local_dest=False):
    options = ""
    if suppress_fingerprint_warnings:
        # Suppress warnings and questions related to host key fingerprints
        # because on a test network IPs get reused, VMs are reinstalled, etc.
        # Based on https://unix.stackexchange.com/a/365976/257493
        options = '-o "StrictHostKeyChecking no" -o "LogLevel ERROR" -o "UserKnownHostsFile /dev/null"'

    if local_dest:
        src = 'root@{}:{}'.format(hostname_or_ip, src)
    else:
        dest = 'root@{}:{}'.format(hostname_or_ip, dest)

    command = "scp {} {} {}".format(options, src, dest)
    res = subprocess.run(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False
    )
    if check and res.returncode:
        raise SSHCommandFailed(res.returncode, res.stdout.decode(), command)

    return res

def to_xapi_bool(b):
    return 'true' if b else 'false'

def parse_xe_dict(xe_dict):
    """
    Parses a xe param containing keys and values, e.g. "major: 7; minor: 20; micro: 0; build: 3".

    Data type remains str for all values.
    """
    res = {}
    for pair in xe_dict.split(';'):
        key, value = pair.split(':')
        res[key.strip()] = value.strip()
    return res

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

    def hosts_uuids(self):
        return self.master.xe('host-list', {}, minimal=True).split(',')

    def host_ip(self, host_uuid):
        return self.master.xe('host-param-get', {'uuid': host_uuid, 'param-name': 'address'})

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

class Host:
    def __init__(self, hostname_or_ip):
        self.hostname_or_ip = hostname_or_ip
        self.inventory = None
        self.uuid = None
        self.xo_srv_id = None
        self.user = None
        self.password = None
        self.saved_packages_list = None
        self.saved_rollback_id = None

    def __str__(self):
        return self.hostname_or_ip

    def initialize(self, pool=None):
        self.inventory = self._get_xensource_inventory()
        self.uuid = self.inventory['INSTALLATION_UUID']
        if self.is_master():
            self.pool = Pool(self)
        else:
            self.pool = pool

    def ssh(self, cmd, check=True, simple_output=True, suppress_fingerprint_warnings=True,
            background=False, decode=True):
        return ssh(self.hostname_or_ip, cmd, check=check, simple_output=simple_output,
                   suppress_fingerprint_warnings=suppress_fingerprint_warnings, background=background,
                   decode=decode)

    def ssh_with_result(self, cmd):
        # doesn't raise if the command's return is nonzero, unless there's a SSH error
        return self.ssh(cmd, check=False, simple_output=False)

    def scp(self, src, dest, check=True, suppress_fingerprint_warnings=True, local_dest=False):
        return scp(
            self.hostname_or_ip, src, dest, check=check,
            suppress_fingerprint_warnings=suppress_fingerprint_warnings, local_dest=local_dest
        )

    def xe(self, action, args={}, check=True, simple_output=True, minimal=False):
        maybe_param_minimal = ['--minimal'] if minimal else []

        def stringify(key, value):
            if type(value) == bool:
                return "{}={}".format(key, to_xapi_bool(value))
            return "{}={}".format(key, shlex.quote(value))

        command = shlex.quote(' '.join(
            ['xe', action] + maybe_param_minimal + [stringify(key, value) for key, value in args.items()]
        ))

        result = self.ssh(
            [command],
            check=check,
            simple_output=simple_output
        )

        if result == 'true':
            return True
        if result == 'false':
            return False
        return result

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
        except SSHCommandFailed:
            # If XAPI is not ready yet, or the host is down, this will throw. We return False in that case.
            return False

    def has_updates(self):
        try:
            # yum check-update returns 100 if there are updates, 1 if there's an error, 0 if no updates
            self.ssh(['yum', 'check-update'])
            # returned 0, else there would have been a SSHCommandFailed
            return False
        except SSHCommandFailed as e:
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
        except SSHCommandFailed as e:
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

    def file_exists(self, filepath):
        return self.ssh_with_result(['test', '-f', filepath]).returncode == 0

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

    def call_plugin(self, plugin_name, function, args=None):
        params = {'host-uuid': self.uuid, 'plugin': plugin_name, 'fn': function}
        if args is not None:
            for k, v in args.items():
                params['args:%s' % k] = v
        return self.xe('host-call-plugin', params)

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
        except SSHCommandFailed as e:
            if key and accept_unknown_key and e.stdout.strip() == "Error: Key %s not found in map" % key:
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

class VM(BaseVM):
    def __init__(self, uuid, host):
        super().__init__(uuid, host)
        self.ip = None
        self.previous_host = None # previous host when migrated or being migrated
        self.is_windows = self.param_get('platform', 'device_id', accept_unknown_key=True) == '0002'
        self.is_uefi = self.param_get('HVM-boot-params', 'firmware', accept_unknown_key=True) == 'uefi'

    def power_state(self):
        return self.param_get('power-state')

    def is_running(self):
        return self.power_state() == 'running'

    def is_halted(self):
        return self.power_state() == 'halted'

    def is_suspended(self):
        return self.power_state() == 'suspended'

    def is_paused(self):
        return self.power_state() == 'paused'

    # By xe design on must be an host name-label
    def start(self, on=None):
        logging.info("Start VM")
        args = {'uuid': self.uuid}
        if on is not None:
            args['on'] = on
        return self.host.xe('vm-start', args)

    def shutdown(self, force=False, verify=False):
        logging.info("Shutdown VM")
        ret = self.host.xe('vm-shutdown', {'uuid': self.uuid, 'force': force})
        if verify:
            wait_for(self.is_halted, "Wait for VM halted")
        return ret

    def reboot(self, force=False, verify=False):
        logging.info("Reboot VM")
        ret = self.host.xe('vm-reboot', {'uuid': self.uuid, 'force': force})
        if verify:
            # No need to verify that the reboot actually happened because the xe command
            # does that for us already (it only finishes once the reboot started).
            # So we just wait for the VM to be operational again
            self.wait_for_vm_running_and_ssh_up()
        return ret

    def try_get_and_store_ip(self):
        ip = self.param_get('networks', '0/ip', accept_unknown_key=True)

        # An IP that starts with 169.254. is not a real routable IP.
        # VMs may return such an IP before they get an actual one from DHCP.
        if not ip or ip.startswith('169.254.'):
            return False
        else:
            logging.info("VM IP: %s" % ip)
            self.ip = ip
            return True

    def ssh(self, cmd, check=True, simple_output=True, background=False, decode=True):
        # raises by default for any nonzero return code
        target_os = "windows" if self.is_windows else "linux"
        return ssh(self.ip, cmd, check=check, simple_output=simple_output, background=background,
                   target_os=target_os, decode=decode)

    def ssh_with_result(self, cmd):
        # doesn't raise if the command's return is nonzero, unless there's a SSH error
        return self.ssh(cmd, check=False, simple_output=False)

    def scp(self, src, dest, check=True, suppress_fingerprint_warnings=True, local_dest=False):
        return scp(
            self.ip, src, dest, check=check,
            suppress_fingerprint_warnings=suppress_fingerprint_warnings,
            local_dest=local_dest
        )

    def is_ssh_up(self):
        try:
            return self.ssh_with_result(['true']).returncode == 0
        except SSHCommandFailed:
            # probably not up yet
            return False

    def is_management_agent_up(self):
        return self.param_get('PV-drivers-version', 'major', accept_unknown_key=True) is not None

    def wait_for_os_booted(self):
        wait_for(self.is_running, "Wait for VM running")
        # waiting for the IP:
        # - allows to make sure the OS actually started (on VMs that have the management agent)
        # - allows to store the IP for future use in the VM object
        wait_for(self.try_get_and_store_ip, "Wait for VM IP")
        # now wait also for the management agent to have started
        wait_for(self.is_management_agent_up, "Wait for management agent up")

    def wait_for_vm_running_and_ssh_up(self):
        self.wait_for_os_booted()
        wait_for(self.is_ssh_up, "Wait for SSH up")

    def ssh_touch_file(self, filepath):
        logging.info("Create file on VM (%s)" % filepath)
        self.ssh(['touch', filepath])
        logging.info("Check file created")
        self.ssh(['test -f ' + filepath])

    def suspend(self, verify=False):
        logging.info("Suspend VM")
        self.host.xe('vm-suspend', {'uuid': self.uuid})
        if verify:
            wait_for(self.is_suspended, "Wait for VM suspended")

    def resume(self):
        logging.info("Resume VM")
        self.host.xe('vm-resume', {'uuid': self.uuid})

    def pause(self, verify=False):
        logging.info("Pause VM")
        self.host.xe('vm-pause', {'uuid': self.uuid})
        if verify:
            wait_for(self.is_paused, "Wait for VM paused")

    def unpause(self):
        logging.info("Unpause VM")
        self.host.xe('vm-unpause', {'uuid': self.uuid})

    def _disk_list(self):
        return self.host.xe('vm-disk-list', {'uuid': self.uuid}, minimal=True)

    def _destroy(self):
        self.host.xe('vm-destroy', {'uuid': self.uuid})

    def destroy(self, verify=False):
        # Note: not using xe vm-uninstall (which would be convenient) because it leaves a VDI behind
        # See https://github.com/xapi-project/xen-api/issues/4145
        if not self.is_halted():
            self.shutdown(force=True)
        super().destroy()
        if verify:
            wait_for_not(self.exists, "Wait for VM destroyed")

    def exists(self):
        return self.host.pool_has_vm(self.uuid)

    def exists_on_previous_pool(self):
        return self.previous_host.pool_has_vm(self.uuid)

    def migrate(self, target_host, sr=None):
        # workaround XO bug where sometimes it loses connection without knowing it
        self.host.pool.master.xo_server_reconnect()
        if target_host.pool != self.host.pool:
            target_host.pool.master.xo_server_reconnect()

        # Sometimes we migrate VMs right after creating them
        # In that case we need to ensure that XO knows about the new VM
        # Else we risk getting a "no such VM" error
        # Thus, let's first wait for XO to know about the VM
        wait_for(lambda: xo_object_exists(self.uuid), "Wait for XO to know about VM %s" % self.uuid)

        msg = "Migrate VM to host %s" % target_host
        params = {
            'vm': self.uuid,
            'targetHost': target_host.uuid
        }
        if sr is not None:
            msg += " (SR: %s)" % sr.uuid
            params['sr'] = sr.uuid
        logging.info(msg)
        xo_cli('vm.migrate', params)
        self.previous_host = self.host
        self.host = target_host

    def snapshot(self):
        logging.info("Snapshot VM")
        return Snapshot(self.host.xe('vm-snapshot', {'uuid': self.uuid,
                                                     'new-name-label': 'Snapshot of %s' % self.uuid}),
                        self.host)

    def checkpoint(self):
        logging.info("Checkpoint VM")
        return Snapshot(self.host.xe('vm-checkpoint', {'uuid': self.uuid,
                                                       'new-name-label': 'Checkpoint of %s' % self.uuid}),
                        self.host)

    def vifs(self):
        _vifs = []
        for vif_uuid in self.host.xe('vif-list', {'vm-uuid': self.uuid}, minimal=True).split(','):
            _vifs.append(VIF(vif_uuid, self))
        return _vifs

    def is_running_on_host(self, host):
        return self.is_running() and self.param_get('resident-on') == host.uuid

    def start_background_process(self, cmd):
        script = "/tmp/bg_process.sh"
        pidfile = "/tmp/bg_process.pid"
        with tempfile.NamedTemporaryFile('w') as f:
            f.writelines([
                'echo $$>%s\n' % pidfile,
                cmd + '\n'
            ])
            f.flush()
            self.scp(f.name, script)
            self.ssh(['sh', script], background=True)
            wait_for(lambda: self.ssh_with_result(['test', '-f', pidfile]),
                     "wait for pid file %s to exist" % pidfile)
            pid = self.ssh(['cat', pidfile])
            self.ssh(['rm', '-f', script])
            self.ssh(['rm', '-f', pidfile])
            return pid

    def pid_exists(self, pid):
        return self.ssh_with_result(['test', '-d', '/proc/%s' % pid]).returncode == 0

    def execute_script(self, script_contents, simple_output=True):
        with tempfile.NamedTemporaryFile('w') as f:
            f.write(script_contents)
            f.flush()
            self.scp(f.name, f.name)
            try:
                res = self.ssh(['sh', f.name], simple_output=simple_output)
                return res
            finally:
                self.ssh(['rm', '-f', f.name])

    def distro(self):
        """
        Returns the distro name as detected by the guest tools.

        If the distro name was not detected, the result will be an empty string.
        """
        script = "eval $(xe-linux-distribution)\n"
        script += "echo $os_distro\n"
        return self.execute_script(script)

    def tools_version_dict(self):
        """
        Returns the guest tools version as detected by the guest tools, as a {major:, minor:, micro:, build:} dict.

        Values are strings.
        """
        return parse_xe_dict(self.param_get('PV-drivers-version'))

    def tools_version(self):
        """ Returns the tools version in the form major.minor.micro-build. """
        version_dict = self.tools_version_dict()
        return "{major}.{minor}.{micro}-{build}".format(**version_dict)

    def file_exists(self, filepath):
        """ Test that the file at filepath exists. """
        return self.ssh_with_result(['test', '-f', filepath]).returncode == 0

    def detect_package_manager(self):
        """ Heuristic to determine the package manager on a unix distro. """
        if self.file_exists('/usr/bin/rpm') or self.file_exists('/bin/rpm'):
            return PackageManagerEnum.RPM
        elif self.file_exists('/usr/bin/apt-get'):
            return PackageManagerEnum.APT_GET
        else:
            return PackageManagerEnum.UNKNOWN

    def mount_guest_tools_iso(self):
        self.host.xe('vm-cd-insert', {'uuid': self.uuid, 'cd-name': 'guest-tools.iso'})

    def unmount_guest_tools_iso(self):
        self.host.xe('vm-cd-eject', {'uuid': self.uuid})

    # *** Common reusable test fragments
    def test_snapshot_on_running_vm(self):
        self.wait_for_vm_running_and_ssh_up()
        snapshot = self.snapshot()
        try:
            filepath = '/tmp/%s' % snapshot.uuid
            self.ssh_touch_file(filepath)
            snapshot.revert()
            self.start()
            self.wait_for_vm_running_and_ssh_up()
            logging.info("Check file does not exist anymore")
            self.ssh(['test ! -f ' + filepath])
        finally:
            snapshot.destroy(verify=True)

    def get_messages(self, name):
        args = {
            'obj-uuid': self.uuid,
            'name': name,
            'params': 'uuid',
        }

        lines = self.host.xe('message-list', args).splitlines()

        # Extracts uuids from lines of: "uuid ( RO) : <uuid>"
        return [e.split(':')[1].strip() for e in lines if e]

    def rm_messages(self, name):
        msgs = self.get_messages(name)

        for msg in msgs:
            self.host.xe('message-destroy', {'uuid': msg})

    def sign_efi_bins(self, db: EFIAuth):
        with tempfile.TemporaryDirectory() as directory:
            for remote_bin in self.get_all_efi_bins():
                local_bin = os.path.join(directory, os.path.basename(remote_bin))
                self.scp(remote_bin, local_bin, local_dest=True)
                signed = db.sign_image(local_bin)
                self.scp(signed, remote_bin)

    def set_efi_var(self, var: str, guid: str, attrs: bytes, data: bytes):
        """Sets the data and attrs for an EFI variable and GUID."""
        assert len(attrs) == 4

        efivarfs = '/sys/firmware/efi/efivars/%s-%s' % (var, guid)

        if self.file_exists(efivarfs):
            self.ssh(['chattr', '-i', efivarfs])

        with tempfile.NamedTemporaryFile('wb') as f:
            f.write(attrs)
            f.write(data)
            f.flush()
            self.scp(f.name, efivarfs)

    def get_efi_var(self, var, guid):
        """Returns a 2-tuple of (attrs, data) for an EFI variable."""
        efivarfs = '/sys/firmware/efi/efivars/%s-%s' % (var, guid)

        if not self.file_exists(efivarfs):
            return 0, b''

        data = self.ssh(['cat', efivarfs], simple_output=False, decode=False).stdout

        # The efivarfs file starts with the attributes, which are 4 bytes long
        return data[:4], data[4:]

    def file_exists(self, filepath):
        """Returns True if the file exists, otherwise returns False."""
        return self.ssh_with_result(['test', '-f', filepath]).returncode == 0

    def sign_bins(self):
        for f in self.get_all_efi_bins():
            self.sign(f)

    def get_all_efi_bins(self):
        magicsz = str(len(EFI_HEADER_MAGIC))
        files = self.ssh(
            [
                'for', 'file', 'in', '$(find', '/boot', '-type', 'f);',
                'do', 'echo', '$file', '$(head', '-c', magicsz, '$file);',
                'done'
            ],
            simple_output=False,
            decode=False).stdout.split(b'\n')

        magic = EFI_HEADER_MAGIC.encode('ascii')
        binaries = []
        for f in files:
            if magic in f:
                # Avoid decoding an unsplit f, as some headers are not utf8
                # decodable
                fpath = f.split()[0].decode('ascii')
                binaries.append(fpath)

        return binaries

    def clone(self):
        name = self.name() + '_clone_for_tests'
        logging.info("Clone VM")
        uuid = self.host.xe('vm-clone', {'uuid': self.uuid, 'new-name-label': name})
        logging.info("New VM: %s (%s)" % (uuid, name))
        return VM(uuid, self.host)

class Snapshot(BaseVM):
    def _disk_list(self):
        return self.host.xe('snapshot-disk-list', {'uuid': self.uuid}, minimal=True)

    def destroy(self, verify=False):
        logging.info("Delete snapshot " + self.uuid)
        # that uninstall command apparently works better for snapshots than for VMs apparently
        self.host.xe('snapshot-uninstall', {'uuid': self.uuid, 'force': True})
        if verify:
            logging.info("Check snapshot doesn't exist anymore")
            assert not self.exists()

#     def _destroy(self):
#         self.host.xe('snapshot-destroy', {'uuid': self.uuid})

    def exists(self):
        return self.host.pool_has_vm(self.uuid, vm_type='snapshot')

    def revert(self):
        logging.info("Revert snapshot")
        self.host.xe('snapshot-revert', {'uuid': self.uuid})

class VIF:
    def __init__(self, uuid, vm):
        self.uuid = uuid
        self.vm = vm

    def move(self, network_uuid):
        self.vm.host.xe('vif-move', {'uuid': self.uuid, 'network-uuid': network_uuid})

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
            except SSHCommandFailed as e:
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

    def content_type(self):
        return self.pool.master.xe('sr-param-get', {'uuid': self.uuid, 'param-name': 'content-type'})

    def is_shared(self):
        return self.pool.master.xe('sr-param-get', {'uuid': self.uuid, 'param-name': 'shared'})
