import atexit
import logging
import tempfile
import time
import xml.etree.ElementTree as ET

from lib import commands, pxe
from lib.commands import local_cmd, ssh
from lib.common import wait_for

from data import HOST_DEFAULT_PASSWORD_HASH
from data import ISO_IMAGES

def clean_files_on_pxe(mac_address):
    logging.info('cleanning file for mac {}'.format(mac_address))
    pxe.server_remove_config(mac_address)

def setup_pxe_boot(vm, mac_address, version):
    with tempfile.TemporaryDirectory(suffix=mac_address) as tmp_local_path:
        logging.info('Generate answerfile.xml for {}'.format(mac_address))

        hdd = 'nvme0n1' if vm.is_uefi else 'sda'
        encrypted_password = HOST_DEFAULT_PASSWORD_HASH
        try:
            installer = ISO_IMAGES[version]['pxe-url']
        except KeyError:
           pytest.skip(f"cannot found pxe-url for {version}")
        pxe_addr = pxe.PXE_CONFIG_SERVER

        with open(f'{tmp_local_path}/answerfile.xml', 'w') as answerfile:
            answerfile.write(f"""<?xml version="1.0"?>
<installation>
    <keymap>fr</keymap>
    <primary-disk>{hdd}</primary-disk>
    <guest-disk>{hdd}</guest-disk>
    <root-password type="hash">{encrypted_password}</root-password>
    <source type="url">{installer}</source>
    <admin-interface name="eth0" proto="dhcp" />
    <timezone>Europe/Paris</timezone>
    <script stage="filesystem-populated" type="url">
        http://{pxe_addr}/configs/presets/scripts/filesystem-populated.py
    </script>
</installation>
            """)

        logging.info('Generate boot.conf for {}'.format(mac_address))
        pxe.generate_boot_conf(tmp_local_path, installer)

        logging.info('Copy files to {}'.format(pxe_addr))
        pxe.server_push_config(mac_address, tmp_local_path)

        logging.info('Register cleanup files for PXE')
        atexit.register(lambda: clean_files_on_pxe(mac_address))

class AnswerFile:
    def __init__(self, kind, /):
        from data import BASE_ANSWERFILES
        defn = BASE_ANSWERFILES[kind]
        self.defn = self._normalize_structure(defn)

    def write_xml(self, filename):
        logging.info("generating answerfile %s", filename)
        etree = ET.ElementTree(self._defn_to_xml_et(self.defn))
        etree.write(filename)

    # chainable mutators for lambdas

    def top_append(self, *defs):
        for defn in defs:
            self.defn['CONTENTS'].append(self._normalize_structure(defn))
        return self

    def top_setattr(self, attrs):
        assert 'CONTENTS' not in attrs
        self.defn.update(attrs)
        return self

    # makes a mutable deep copy of all `contents`
    @staticmethod
    def _normalize_structure(defn):
        assert isinstance(defn, dict)
        assert 'TAG' in defn
        defn = dict(defn)
        if 'CONTENTS' not in defn:
            defn['CONTENTS'] = []
        if not isinstance(defn['CONTENTS'], str):
            defn['CONTENTS'] = [AnswerFile._normalize_structure(item)
                                for item in defn['CONTENTS']]
        return defn

    # convert to a ElementTree.Element tree suitable for further
    # modification before we serialize it to XML
    @staticmethod
    def _defn_to_xml_et(defn, /, *, parent=None):
        assert isinstance(defn, dict)
        defn = dict(defn)
        name = defn.pop('TAG')
        assert isinstance(name, str)
        contents = defn.pop('CONTENTS', ())
        assert isinstance(contents, (str, list))
        element = ET.Element(name, **defn)
        if parent is not None:
            parent.append(element)
        if isinstance(contents, str):
            element.text = contents
        else:
            for contents in contents:
                AnswerFile._defn_to_xml_et(contents, parent=element)
        return element

def poweroff(ip):
    try:
        ssh(ip, ["poweroff"])
    except commands.SSHCommandFailed as e:
        # ignore connection closed by reboot
        if e.returncode == 255 and "closed by remote host" in e.stdout:
            logging.info("sshd closed the connection")
            pass
        else:
            raise

def monitor_install(*, ip, is_pxe=False):
    if is_pxe:
        # When using PXE boot the IP is available but ssh is not so we just
        # need to wait.
        while True:
            try:
                ssh(ip, ["ls"], check=False)
            except commands.SSHCommandFailed as e:
                if e.returncode == 255:
                    logging.debug("ssh connexion refused, wait 10s...")
                    time.sleep(10)
                else:
                    # Raise all other errors
                    raise e
            else:
                logging.debug("ssh works")
                break
    else:
        # wait for "yum install" phase to finish
        wait_for(lambda: ssh(ip, ["grep",
                                  "'DISPATCH: NEW PHASE: Completing installation'",
                                  "/tmp/install-log"],
                             check=False, simple_output=False,
                             ).returncode == 0,
                 "Wait for rpm installation to succeed",
                 timeout_secs=40*60) # FIXME too big

        # wait for install to finish
        wait_for(lambda: ssh(ip, ["grep",
                                  "'The installation completed successfully'",
                                  "/tmp/install-log"],
                             check=False, simple_output=False,
                             ).returncode == 0,
                 "Wait for system installation to succeed",
                 timeout_secs=40*60) # FIXME too big

    wait_for(lambda: ssh(ip, ["ps a|grep '[0-9]. python /opt/xensource/installer/init'"],
                         check=False, simple_output=False,
                         ).returncode == 1,
             "Wait for installer to terminate")

def perform_install(*, iso, host_vm, version=None):
    vif = host_vm.vifs()[0]
    mac_address = vif.param_get('MAC')
    logging.info("Host VM has MAC %s", mac_address)

    if iso:
        host_vm.insert_cd(iso)
    else:
        setup_pxe_boot(host_vm, mac_address, version)

    try:
        host_vm.start()
        wait_for(host_vm.is_running, "Wait for host VM running")

        # catch host-vm IP address
        wait_for(lambda: pxe.arp_addresses_for(mac_address),
                 "Wait for DHCP server to see Host VM in ARP tables",
                 timeout_secs=10*60)
        ips = pxe.arp_addresses_for(mac_address)
        logging.info("Host VM has IPs %s", ips)
        assert len(ips) == 1
        host_vm.ip = ips[0]

        monitor_install(ip=host_vm.ip, is_pxe = False if iso else True)

        logging.info("Shutting down Host VM after successful installation")
        poweroff(host_vm.ip)
        wait_for(host_vm.is_halted, "Wait for host VM halted")
        if iso:
            host_vm.eject_cd()

    except Exception as e:
        logging.critical("caught exception %s", e)
        # wait_for(lambda: False, 'Wait "forever"', timeout_secs=100*60)
        #host_vm.shutdown(force=True)
        raise
    except KeyboardInterrupt:
        logging.warning("keyboard interrupt")
        # wait_for(lambda: False, 'Wait "forever"', timeout_secs=100*60)
        #host_vm.shutdown(force=True)
        raise

def monitor_upgrade(*, ip):
    # wait for "yum install" phase to start
    wait_for(lambda: ssh(ip, ["grep",
                              "'DISPATCH: NEW PHASE: Reading package information'",
                              "/tmp/install-log"],
                         check=False, simple_output=False,
                         ).returncode == 0,
             "Wait for upgrade preparations to finish",
             timeout_secs=40*60) # FIXME too big

    # wait for "yum install" phase to finish
    wait_for(lambda: ssh(ip, ["grep",
                              "'DISPATCH: NEW PHASE: Completing installation'",
                              "/tmp/install-log"],
                         check=False, simple_output=False,
                         ).returncode == 0,
             "Wait for rpm installation to succeed",
             timeout_secs=40*60) # FIXME too big

    # wait for install to finish
    wait_for(lambda: ssh(ip, ["grep",
                              "'The installation completed successfully'",
                              "/tmp/install-log"],
                         check=False, simple_output=False,
                         ).returncode == 0,
             "Wait for system installation to succeed",
             timeout_secs=40*60) # FIXME too big

    wait_for(lambda: ssh(ip, ["ps a|grep '[0-9]. python /opt/xensource/installer/init'"],
                         check=False, simple_output=False,
                         ).returncode == 1,
             "Wait for installer to terminate")

def perform_upgrade(*, iso, host_vm):
    vif = host_vm.vifs()[0]
    mac_address = vif.param_get('MAC')
    logging.info("Host VM has MAC %s", mac_address)

    host_vm.insert_cd(iso)

    try:
        pxe.arp_clear_for(mac_address)

        host_vm.start()
        wait_for(host_vm.is_running, "Wait for host VM running")

        # catch host-vm IP address
        wait_for(lambda: pxe.arp_addresses_for(mac_address),
                 "Wait for DHCP server to see Host VM in ARP tables",
                 timeout_secs=10*60)
        ips = pxe.arp_addresses_for(mac_address)
        logging.info("Host VM has IPs %s", ips)
        assert len(ips) == 1
        host_vm.ip = ips[0]

        # host may not be up if ARP cache was filled
        wait_for(lambda: local_cmd(["ping", "-c1", host_vm.ip], check=False),
                 "Wait for host up", timeout_secs=10 * 60, retry_delay_secs=10)
        wait_for(lambda: local_cmd(["nc", "-zw5", host_vm.ip, "22"], check=False),
                 "Wait for ssh up on host", timeout_secs=10 * 60, retry_delay_secs=5)

        monitor_upgrade(ip=host_vm.ip)

        logging.info("Shutting down Host VM after successful upgrade")
        poweroff(host_vm.ip)
        wait_for(host_vm.is_halted, "Wait for host VM halted")

    except Exception as e:
        logging.critical("caught exception %s", e)
        # wait_for(lambda: False, 'Wait "forever"', timeout_secs=100*60)
        #host_vm.shutdown(force=True)
        raise
    except KeyboardInterrupt:
        logging.warning("keyboard interrupt")
        # wait_for(lambda: False, 'Wait "forever"', timeout_secs=100*60)
        #host_vm.shutdown(force=True)
        raise

    host_vm.eject_cd()
