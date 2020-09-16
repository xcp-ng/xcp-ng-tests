import json
import subprocess
import time
from subprocess import CalledProcessError
from uuid import UUID

# Common VM images used in tests
def vm_image(vm_key):
    from data import VM_IMAGES, DEF_VM_URL
    url = VM_IMAGES[vm_key]
    if not url.startswith('http'):
        url = DEF_VM_URL + url
    return url

def wait_for(fn, msg=None, timeout_secs=120, retry_delay_secs=2, invert=False):
    if msg is not None:
        print(msg)
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

class SSHException(Exception):
    pass

def ssh(hostname_or_ip, cmd, check=True, simple_output=True, suppress_fingerprint_warnings=True, background=False):
    options = ""
    if suppress_fingerprint_warnings:
        # Suppress warnings and questions related to host key fingerprints
        # because on a test network IPs get reused, VMs are reinstalled, etc.
        # Based on https://unix.stackexchange.com/a/365976/257493
        options = '-o "StrictHostKeyChecking no" -o "LogLevel ERROR" -o "UserKnownHostsFile /dev/null"'

    command = " ".join(cmd)
    if background:
        # https://stackoverflow.com/questions/29142/getting-ssh-to-execute-a-command-in-the-background-on-target-machine
        command = "nohup %s &>/dev/null &" % command
    res = subprocess.run(
        "ssh root@%s %s '%s'" % (hostname_or_ip, options, command),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check
    )

    # Even if check is False, we still raise in case of return code 255, which means a SSH error.
    if res.returncode == 255:
        raise SSHException("SSH Error: %s" % res.stdout.decode())

    if simple_output:
        return res.stdout.decode().strip()
    else:
        return res

def to_xapi_bool(b):
    return 'true' if b else 'false'

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

class Host:
    def __init__(self, hostname_or_ip):
        self.hostname_or_ip = hostname_or_ip
        self.inventory = None
        self.uuid = None
        self.xo_srv_id = None
        self.user = None
        self.password = None

    def __str__(self):
        return self.hostname_or_ip

    def initialize(self, pool=None):
        self.inventory = self._get_xensource_inventory()
        self.uuid = self.inventory['INSTALLATION_UUID']
        if self.is_master():
            self.pool = Pool(self)
        else:
            self.pool = pool

    def ssh(self, cmd, check=True, simple_output=True, suppress_fingerprint_warnings=True, background=False):
        return ssh(self.hostname_or_ip, cmd, check=check, simple_output=simple_output,
                   suppress_fingerprint_warnings=suppress_fingerprint_warnings, background=background)

    def ssh_with_result(self, cmd):
        # doesn't raise if the command's return is nonzero, unless there's a SSH error
        return self.ssh(cmd, check=False, simple_output=False)

    def xe(self, action, args={}, check=True, simple_output=True, minimal=False):
        maybe_param_minimal = ['--minimal'] if minimal else []
        return self.ssh(
            ['xe', action]  + maybe_param_minimal + ["%s=%s" % (key, value) for key, value in args.items()],
            check=check,
            simple_output=simple_output
        )

    def _get_xensource_inventory(self):
        output = self.ssh(['cat', '/etc/xensource-inventory'])
        inventory = {}
        for line in output.splitlines():
            key, raw_value = line.split('=')
            inventory[key] = raw_value.strip('\'')
        return inventory

    def xo_server_remove(self):
        if self.xo_srv_id is not None:
            xo_cli('server.remove', {'id': self.xo_srv_id})
        else:
            servers = json.loads(xo_cli('server.getAll'))
            for server in servers:
                if server['host'] == self.hostname_or_ip:
                    xo_cli('server.remove', {'id': server['id']})

    def xo_server_add(self, username, password, label=None, unregister_first=True):
        """
        Returns the server ID created by XO's server.add
        """
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

    def import_vm_url(self, url, sr_uuid=None):
        print("Import VM %s on host %s" % (url, self))
        params = {
            'url': url
        }
        if sr_uuid is not None:
            params['sr-uuid'] = sr_uuid
        vm = VM(self.xe('vm-import', params), self)
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
        print("Install updates on host %s" % self)
        return self.ssh(['yum', 'update', '-y'])

    def restart_toolstack(self):
        print("Restart toolstack on host %s" % self)
        return self.ssh(['xe-toolstack-restart'])

    def is_enabled(self):
        try:
            return self.xe('host-param-get', {'uuid': self.uuid, 'param-name': 'enabled'}) == 'true'
        except subprocess.CalledProcessError:
            # If XAPI is not ready yet, or the host is down, this will throw. We return False in that case.
            return False

    def has_updates(self):
        try:
            # yum check-update returns 100 if there are updates, 1 if there's an error, 0 if no updates
            self.ssh(['yum', 'check-update'])
            # returned 0, else there would have been a CalledProcessError
            return False
        except CalledProcessError as e:
            if e.returncode == 100:
                return True
            else:
                raise

    def yum_install(self, packages):
        print('Install packages: %s on host %s' % (' '.join(packages), self))
        return self.ssh(['yum', 'install', '-y'] + packages)

    def yum_remove(self, packages):
        print('Remove packages: %s from host %s' % (' '.join(packages), self))
        return self.ssh(['yum', 'remove', '-y'] + packages)

    def reboot(self, verify=False):
        print("Reboot host %s" % self)
        try:
            self.ssh(['reboot'])
        except subprocess.CalledProcessError as e:
            # ssh connection may get killed by the reboot and terminate with an error code
            if "closed by remote host" in e.stdout.decode().strip():
                pass
        if verify:
            wait_for_not(self.is_enabled, "Wait for host down")
            wait_for(self.is_enabled, "Wait for host up", timeout_secs=300)

    def management_network(self):
        return self.xe('network-list', {'bridge': self.inventory['MANAGEMENT_INTERFACE']}, minimal=True)

    def disks(self):
        """ List of SCSI disks, e.g ['sda', 'sdb'] """
        disks = self.ssh(['lsblk', '-nd', '-I', '8', '--output', 'NAME']).splitlines()
        disks.sort()
        return disks

    def file_exists(self, filepath):
        return self.ssh_with_result(['test', '-f', filepath]).returncode == 0

    def sr_create(self, sr_type, device, label):
        params = {
            'host-uuid': self.uuid,
            'type': sr_type,
            'name-label': label,
            'device-config:device': device,
            'content-type': 'user'
        }
        print("Create %s SR on host %s's %s device with label '%s'" % (sr_type, self, device, label))
        sr_uuid = self.xe('sr-create', params)
        return SR(sr_uuid, self.pool)

    def is_master(self):
        return self.ssh(['cat', '/etc/xensource/pool.conf']) == 'master'

    def local_vm_srs(self):
        srs = []
        for sr_uuid in self.xe('pbd-list', {'host-uuid': self.uuid, 'params': 'sr-uuid'}, minimal=True).split(','):
            sr = SR(sr_uuid, self.pool)
            if sr.content_type() == 'user' and not sr.is_shared():
                srs.append(sr)
        return srs

class BaseVM:
    """ Base class for VM and Snapshot """
    def __init__(self, uuid, host):
        self.uuid = uuid
        self.host = host

    def param_get(self, param_name, key=None, accept_unknown_key=False):
        args = {'uuid': self.uuid, 'param-name': param_name}
        if key is not None:
            args['param-key'] = key
        try:
            value = self.host.xe('vm-param-get', args)
        except subprocess.CalledProcessError as e:
            if key and accept_unknown_key and e.stdout.decode().strip() == "Error: Key %s not found in map" % key:
                value = None
            else:
                raise
        return value

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

class VM(BaseVM):
    def __init__(self, uuid, host):
        super().__init__(uuid, host)
        self.ip = None
        self.previous_host = None # previous host when migrated or being migrated

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

    def start(self):
        print("Start VM")
        return self.host.xe('vm-start', {'uuid': self.uuid})

    def shutdown(self, force=False, verify=False):
        print("Shutdown VM")
        return self.host.xe('vm-shutdown', {'uuid': self.uuid, 'force': to_xapi_bool(force)})
        if verify:
            wait_for(self.is_halted, "Wait for VM halted")

    def try_get_and_store_ip(self):
        ip = self.param_get('networks', '0/ip', accept_unknown_key=True)

        if not ip:
            return False
        else:
            self.ip = ip
            return True

    def ssh(self, cmd, check=True, simple_output=True, background=False):
        # raises by default for any nonzero return code
        return ssh(self.ip, cmd, check=check, simple_output=simple_output, background=background)

    def ssh_with_result(self, cmd):
        # doesn't raise if the command's return is nonzero, unless there's a SSH error
        return self.ssh(cmd, check=False, simple_output=False)

    def is_ssh_up(self):
        try:
            return self.ssh_with_result(['true']).returncode == 0
        except SSHException:
            # probably not up yet
            return False

    def wait_for_os_booted(self, wait_for_ip=True):
        wait_for(self.is_running, "Wait for VM running")
        if wait_for_ip:
            # waiting for the IP:
            # - allows to make sure the OS actually started (on VMs that have the management agent)
            # - allows to store the IP for future use in the VM object
            wait_for(self.try_get_and_store_ip, "Wait for VM IP")

    def wait_for_linux_vm_running_and_ssh_up(self, wait_for_ip=True):
        self.wait_for_os_booted(wait_for_ip)
        assert self.ip is not None
        wait_for(self.is_ssh_up, "Wait for SSH up")

    def ssh_touch_file(self, filepath):
        print("Create file on VM (%s)" % filepath)
        self.ssh(['touch', filepath])
        print("Check file created")
        self.ssh(['test -f ' + filepath])

    def suspend(self, verify=False):
        print("Suspend VM")
        self.host.xe('vm-suspend', {'uuid': self.uuid})
        if verify:
            wait_for(self.is_suspended, "Wait for VM suspended")

    def resume(self):
        print("Resume VM")
        self.host.xe('vm-resume', {'uuid': self.uuid})

    def pause(self, verify=False):
        print("Pause VM")
        self.host.xe('vm-pause', {'uuid': self.uuid})
        if verify:
            wait_for(self.is_paused, "Wait for VM paused")

    def unpause(self):
        print("Unpause VM")
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
        msg = "Migrate VM to host %s" % target_host
        params = {
            'vm': self.uuid,
            'targetHost': target_host.uuid
        }
        if sr is not None:
            msg += " (SR: %s)" % sr.uuid
            params['sr'] = sr.uuid
        print(msg)
        xo_cli('vm.migrate', params)
        self.previous_host = self.host
        self.host = target_host

    def snapshot(self):
        print("Snapshot VM")
        return Snapshot(self.host.xe('vm-snapshot', {'uuid': self.uuid,
                                                     'new-name-label': '"Snapshot of %s"' % self.uuid}),
                        self.host)

    def checkpoint(self):
        print("Checkpoint VM")
        return Snapshot(self.host.xe('vm-checkpoint', {'uuid': self.uuid,
                                                     'new-name-label': '"Checkpoint of %s"' % self.uuid}),
                        self.host)

    def vifs(self):
        _vifs = []
        for vif_uuid in self.host.xe('vif-list', {'vm-uuid': self.uuid}, minimal=True).split(','):
            _vifs.append(VIF(vif_uuid, self))
        return _vifs


    # *** Common reusable tests

    def test_snapshot_on_linux_vm(self):
        snapshot = self.snapshot()
        try:
            filepath = '/tmp/%s' % snapshot.uuid
            self.ssh_touch_file(filepath)
            snapshot.revert()
            self.start()
            self.wait_for_linux_vm_running_and_ssh_up()
            print("Check file does not exist anymore")
            self.ssh(['test ! -f ' + filepath])
        finally:
            snapshot.destroy(verify=True)


class Snapshot(BaseVM):
    def _disk_list(self):
        return self.host.xe('snapshot-disk-list', {'uuid': self.uuid}, minimal=True)

    def destroy(self, verify=False):
        print("Delete snapshot " + self.uuid)
        # that uninstall command apparently works better for snapshots than for VMs apparently
        self.host.xe('snapshot-uninstall', {'uuid': self.uuid, 'force': 'true'})
        if verify:
            print("Check snapshot doesn't exist anymore")
            assert not self.exists()

#     def _destroy(self):
#         self.host.xe('snapshot-destroy', {'uuid': self.uuid})

    def exists(self):
        return self.host.pool_has_vm(self.uuid, vm_type='snapshot')

    def revert(self):
        print("Revert snapshot")
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

    def unplug_pbds(self):
        print("Unplug PBDs")
        for pbd_uuid in self.pbd_uuids():
            self.pool.master.xe('pbd-unplug', {'uuid': pbd_uuid})

    def all_pbds_attached(self):
        all_attached = True
        for pbd_uuid in self.pbd_uuids():
            all_attached = all_attached and self.pool.master.xe('pbd-param-get', {'uuid': pbd_uuid, 'param-name': 'currently-attached'}) == 'true'
        return all_attached

    def plug_pbds(self, verify=True):
        print("Attach PBDs")
        for pbd_uuid in self.pbd_uuids():
            self.pool.master.xe('pbd-plug', {'uuid': pbd_uuid})
        if verify:
            wait_for(self.all_pbds_attached, "Wait for PDBs attached")

    def destroy(self, verify=False):
        self.unplug_pbds()
        print("Destroy SR " + self.uuid)
        self.pool.master.xe('sr-destroy', {'uuid': self.uuid})
        if verify:
            wait_for_not(self.exists, "Wait for SR destroyed")

    def forget(self):
        self.unplug_pbds()
        print("Forget SR " + self.uuid)
        self.pool.master.xe('sr-forget', {'uuid': self.uuid})

    def exists(self):
        return self.pool.master.xe('sr-list', {'uuid': self.uuid}, minimal=True) == self.uuid

    def scan(self):
        print("Scan SR " + self.uuid)
        self.pool.master.xe('sr-scan', {'uuid': self.uuid})

    def hosts_uuids(self):
        return self.pool.master.xe('pbd-list', {'sr-uuid': self.uuid, 'params': 'host-uuid'}, minimal=True).split(',')

    def attached_to_host(self, host):
        return host.uuid in self.hosts_uuids()

    def content_type(self):
        return self.pool.master.xe('sr-param-get', {'uuid': self.uuid, 'param-name': 'content-type'})

    def is_shared(self):
        return self.pool.master.xe('sr-param-get', {'uuid': self.uuid, 'param-name': 'shared'}) == 'true'
