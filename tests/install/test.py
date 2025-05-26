import logging
import pytest
from uuid import uuid4
from typing import cast

from lib import commands, installer, pxe
from lib.common import safe_split, wait_for
from lib.installer import AnswerFile
from lib.pif import PIF
from lib.pool import Pool
from lib.vdi import VDI

from data import HOSTS_IP_CONFIG, ISO_IMAGES, NETWORKS
assert "MGMT" in NETWORKS
assert "HOSTS" in HOSTS_IP_CONFIG

# Requirements:
# - one XCP-ng host capable of nested virt, with an ISO SR, and a default SR
# - the "small_vm" ISO must have in authorized_keys a SSH key accepted by the
#   ssh server in the installed host version (7.x and earlier reject current
#   ssh-rsa keys, a public ssh-ed25519 key listed in TEST_SSH_PUBKEY should be
#   there)

@pytest.fixture
def helper_vm_with_plugged_disk(running_vm, create_vms):
    helper_vm = running_vm
    host_vm, = create_vms

    all_vdis = [VDI(uuid, host=host_vm.host) for uuid in host_vm.vdi_uuids()]
    disk_vdis = [vdi for vdi in all_vdis if not vdi.readonly()]

    vbds = [helper_vm.create_vbd(str(n + 1), vdi.uuid) for n, vdi in enumerate(disk_vdis)]
    try:
        for vbd in vbds:
            vbd.plug()

        yield helper_vm

    finally:
        for vbd in reversed(vbds):
            vbd.unplug()
            vbd.destroy()

@pytest.mark.dependency()
class TestNested:
    @pytest.mark.parametrize("admin_iface", ("ipv4dhcp", "ipv4static", "ipv6static", "ipv6ac", "ipv6dhcp"))
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("package_source", ("iso", "net"))
    @pytest.mark.parametrize("system_disk_config", ("disk", "raid1"))
    @pytest.mark.parametrize("iso_version", (
        "83nightly", "830net",
        "830",
        "82nightly",
        "821.1",
        "81", "80", "76", "75",
        "xs8", "ch821.1",
        "xs70",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.vm_definitions(
        lambda firmware, system_disk_config: dict(
            name="vm1",
            template="Other install media",
            params=(
                # dict(param_name="", value=""),
                dict(param_name="memory-static-max", value="4GiB"),
                dict(param_name="memory-dynamic-max", value="4GiB"),
                dict(param_name="memory-dynamic-min", value="4GiB"),
                dict(param_name="VCPUs-max", value="2"),
                dict(param_name="VCPUs-at-startup", value="2"),
                dict(param_name="platform", key="exp-nested-hvm", value="true"), # FIXME < 8.3 host?
                dict(param_name="platform", key="nested-virt", value="true"), # FIXME >= 8.3 host?
                dict(param_name="HVM-boot-params", key="order", value="dc"),
            ) + {
                "uefi": (
                    dict(param_name="HVM-boot-params", key="firmware", value="uefi"),
                    dict(param_name="platform", key="device-model", value="qemu-upstream-uefi"),
                ),
                "bios": (),
            }[firmware],
            vdis=([dict(name="vm1 system disk", size="100GiB", device="xvda", userdevice="0")]
                  + ([dict(name="vm1 system disk mirror", size="100GiB", device="xvdb", userdevice="1")]
                     if system_disk_config == "raid1" else [])
                  ),
            cd_vbd=dict(device="xvdd", userdevice="3"),
            vifs=[dict(index=0, network_name=NETWORKS["MGMT"])],
        ))
    @pytest.mark.answerfile(
        lambda system_disks_names, local_sr, package_source, system_disk_config, iso_version,
        admin_iface: AnswerFile("INSTALL")
        .top_setattr({} if local_sr == "nosr" else {"sr-type": local_sr})
        .top_append(
            {"iso": {"TAG": "source", "type": "local"},
             "net": {"TAG": "source", "type": "url",
                     "CONTENTS": ISO_IMAGES[iso_version]['net-url']},
             }[package_source],

            {"raid1": {"TAG": "raid", "device": "md127",
                       "CONTENTS": [
                           {"TAG": "disk", "CONTENTS": diskname} for diskname in system_disks_names
                       ]},
             "disk": None,
             }[system_disk_config],

            {"TAG": "admin-interface", "name": "eth0",
             "proto": ("dhcp" if admin_iface == "ipv4dhcp"
                       else "static" if admin_iface == "ipv4static"
                       else "none"),
             "protov6": ("dhcp" if admin_iface == "ipv6dhcp"
                         else "autoconf" if admin_iface == "ipv6ac"
                         else "static" if admin_iface == "ipv6static"
                         else "none"),
             "CONTENTS": (((
                 {"TAG": "ipaddr", "CONTENTS": cast(str, HOSTS_IP_CONFIG['HOSTS']['DEFAULT'])},
                 {"TAG": "subnet", "CONTENTS": cast(str, HOSTS_IP_CONFIG['NETMASK'])},
                 {"TAG": 'gateway', "CONTENTS": cast(str, HOSTS_IP_CONFIG['GATEWAY'])},
             ) if admin_iface == "ipv4static"
             else ())
                          + ((
                              {"TAG": "ipv6", "CONTENTS": cast(str, HOSTS_IP_CONFIG['HOSTS']['DEFAULT_v6'])},
                              {"TAG": "gatewayv6", "CONTENTS": cast(str, HOSTS_IP_CONFIG['GATEWAY_v6'])},
                          ) if admin_iface == "ipv6static"
             else ())),
             },
            {"TAG": "name-server", "CONTENTS": cast(str, HOSTS_IP_CONFIG['DNS'])} if admin_iface == "ipv4static"
            else None,
            {"TAG": "name-server", "CONTENTS": cast(str, HOSTS_IP_CONFIG['DNS_v6'])} if admin_iface == "ipv6static"
            else None,

            {"TAG": "primary-disk",
             "guest-storage": "no" if local_sr == "nosr" else "yes",
             "CONTENTS": {"disk": system_disks_names[0],
                          "raid1": "md127",
                          }[system_disk_config],
             },
        ))
    def test_install(self, vm_booted_with_installer, system_disks_names,
                     firmware, iso_version, package_source, system_disk_config, local_sr, admin_iface):
        host_vm = vm_booted_with_installer
        installer.monitor_install(ip=host_vm.ip)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("admin_iface", ("ipv4dhcp", "ipv4static", "ipv6static", "ipv6ac", "ipv6dhcp"))
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("package_source", ("iso", "net"))
    @pytest.mark.parametrize("system_disk_config", ("disk", "raid1"))
    @pytest.mark.parametrize("machine", ("host1", "host2"))
    @pytest.mark.parametrize("version", (
        "83nightly", "830net",
        "830",
        "82nightly",
        "821.1",
        "81", "80",
        "76", "75",
        "xs8", "ch821.1",
        "xs70",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(
        lambda version, firmware, local_sr, admin_iface, package_source, system_disk_config: [dict(
            vm="vm1",
            image_test=(f"TestNested::test_install[{firmware}-{version}-{system_disk_config}"
                        f"-{package_source}-{local_sr}-{admin_iface}]"))])
    def test_tune_firstboot(self, create_vms, helper_vm_with_plugged_disk,
                            firmware, version, machine, local_sr, admin_iface, package_source, system_disk_config):
        helper_vm = helper_vm_with_plugged_disk

        if system_disk_config == "disk":
            helper_vm.ssh(["mount /dev/xvdb1 /mnt"])
        elif system_disk_config == "raid1":
            # FIXME helper VM has to be an Alpine, that should not be a random vm_ref
            helper_vm.ssh(["apk add mdadm"])
            helper_vm.ssh(["mdadm -A /dev/md/127 -N localhost:127"])
            helper_vm.ssh(["mount /dev/md127p1 /mnt"])
        else:
            raise ValueError(f"unhandled system_disk_config {system_disk_config!r}")

        try:
            # hostname
            logging.info("Setting hostname to %r", machine)
            helper_vm.ssh(["echo > /mnt/etc/hostname", machine])
            if admin_iface == "ipv4static" and machine in HOSTS_IP_CONFIG['HOSTS']:
                # static management IPv4 if not the default set during install
                ip = HOSTS_IP_CONFIG['HOSTS'][machine]
                logging.info("Changing IP to %s", ip)

                helper_vm.ssh([f"sed -i s/^IP=.*/IP='{ip}'/",
                               "/mnt/etc/firstboot.d/data/management.conf"])
            machine_v6 = f"{machine}_v6"
            if admin_iface == "ipv6static" and machine_v6 in HOSTS_IP_CONFIG['HOSTS']:
                # static management IPv6 if not the default set during install
                ip = HOSTS_IP_CONFIG['HOSTS'][machine_v6]
                logging.info("Changing IP to %s", ip)

                helper_vm.ssh([f"sed -i s/^IPv6=.*/IPv6='{ip}'/",
                               "/mnt/etc/firstboot.d/data/management.conf"])
            # UUIDs
            logging.info("Randomizing UUIDs")
            helper_vm.ssh(
                ['sed -i',
                 f'''-e "/^INSTALLATION_UUID=/ s/.*/INSTALLATION_UUID='{uuid4()}'/"''',
                 f'''-e "/^CONTROL_DOMAIN_UUID=/ s/.*/CONTROL_DOMAIN_UUID='{uuid4()}'/"''',
                 '/mnt/etc/xensource-inventory'])
            helper_vm.ssh(["grep UUID /mnt/etc/xensource-inventory"])
        finally:
            helper_vm.ssh(["umount /mnt"])

    def _test_firstboot(self, create_vms, mode, admin_iface, *, machine='DEFAULT', is_restore=False):
        host_vm = create_vms[0]
        vif = host_vm.vifs()[0]
        mac_address = vif.param_get('MAC')
        logging.info("Host VM has MAC %s", mac_address)

        # succession of insta/upg/rst operations
        split_mode = mode.split("-")
        if is_restore:
            # restore: back to previous installed version
            expected_rel_id = split_mode[-3]
        else:
            expected_rel_id = split_mode[-1]
        expected_rel = {
            "83nightly": "8.3.0",
            "830net": "8.3.0",
            "830": "8.3.0",
            "82nightly": "8.2.1",
            "821.1": "8.2.1",
            "81": "8.1.0",
            "80": "8.0.0",
            "76": "7.6.0",
            "75": "7.5.0",
            #
            "xs8": "8.4.0",
            "ch821.1": "8.2.1",
            "xs70": "7.0.0-125380c",
        }[expected_rel_id]

        # determine version info from `mode`
        if expected_rel_id.startswith("xs"):
            expected_dist = "XenServer"
        elif expected_rel_id.startswith("ch"):
            expected_dist = "CitrixHypervisor"
        else:
            expected_dist = "XCP-ng"

        try:
            host_vm.start()
            wait_for(host_vm.is_running, "Wait for host VM running")

            machine_v6 = f"{machine}_v6"
            if admin_iface in ("ipv4dhcp", "ipv6dhcp", "ipv6ac"):
                # catch host-vm IP address
                wait_for(lambda: pxe.arp_addresses_for(mac_address),
                         "Wait for ARP_SERVER to see Host VM in ARP tables",
                         timeout_secs=10 * 60)
                ips = pxe.arp_addresses_for(mac_address)
                logging.info("Host VM has IPs %s", ips)
                assert len(ips) == 1
                host_vm.ip = ips[0]
            elif admin_iface == "ipv4static":
                host_vm.ip = HOSTS_IP_CONFIG['HOSTS'].get(machine,
                                                          HOSTS_IP_CONFIG['HOSTS']['DEFAULT'])
                logging.info("Expecting host VM to have IP %s", host_vm.ip)
            elif admin_iface == "ipv6static":
                host_vm.ip, prefix_len = HOSTS_IP_CONFIG['HOSTS'].get(
                    machine_v6, HOSTS_IP_CONFIG['HOSTS']['DEFAULT_v6']).split('/')
                logging.info("Expecting host VM to have IP %s", host_vm.ip)
            else:
                raise ValueError(f"admin_iface {admin_iface!r}")

            wait_for(
                lambda: commands.local_cmd(
                    ["nc", "-zw5", host_vm.ip, "22"], check=False).returncode == 0,
                "Wait for ssh back up on Host VM", retry_delay_secs=5, timeout_secs=4 * 60)

            logging.info("Checking installed version (expecting %r %r)",
                         expected_dist, expected_rel)
            lsb_dist = commands.ssh(host_vm.ip, ["lsb_release", "-si"])
            lsb_rel = commands.ssh(host_vm.ip, ["lsb_release", "-sr"])
            assert (lsb_dist, lsb_rel) == (expected_dist, expected_rel)

            # pool master must be reachable here
            pool = Pool(host_vm.ip)

            # wait for XAPI
            wait_for(pool.master.is_enabled, "Wait for XAPI to be ready", timeout_secs=30 * 60)

            if lsb_rel in ["8.2.1", "8.3.0", "8.4.0"]:
                SERVICES = ["control-domain-params-init",
                            "network-init",
                            "storage-init",
                            "generate-iscsi-iqn",
                            "create-guest-templates",
                            ]
                STAMPS_DIR = "/var/lib/misc"
                STAMPS = [f"ran-{service}" for service in SERVICES]
            elif lsb_rel in ["7.0.0-125380c", "7.5.0", "7.6.0", "8.0.0", "8.1.0"]:
                SERVICES = ["xs-firstboot"]
                STAMPS_DIR = "/etc/firstboot.d/state"
                STAMPS = [
                    "10-prepare-storage",
                    "15-set-default-storage",
                    "20-udev-storage",
                    "25-multipath",
                    "40-generate-iscsi-iqn",
                    "50-prepare-control-domain-params",
                    "60-upgrade-likewise-to-pbis",
                    "90-flush-pool-db",
                    "95-legacy-logrotate",
                    "99-remove-firstboot-flag",
                ]
                if lsb_rel in ["7.0.0-125380c"]:
                    STAMPS += [
                        "61-regenerate-old-templates",
                    ]
                if lsb_rel in ["7.5.0", "7.6.0", "8.0.0", "8.1.0"]:
                    STAMPS += [
                        "05-prepare-networking",
                        "60-import-keys",
                        "62-create-guest-templates",
                    ]
                if lsb_rel in ["8.0.0", "8.1.0"]:
                    STAMPS += [
                        "80-common-criteria",
                    ]
            else:
                raise AssertionError(f"Unhandled LSB release {lsb_rel!r}")
            # check for firstboot issues
            # FIXME: flaky, must check logs extraction on failure
            try:
                for stamp in sorted(STAMPS):
                    wait_for(lambda: pool.master.ssh(["test", "-e", f"{STAMPS_DIR}/{stamp}"],
                                                     check=False, simple_output=False,
                                                     ).returncode == 0,
                             f"Wait for {stamp} stamp")
            except TimeoutError:
                logging.warning("investigating lack of %s service stamp", stamp)
                for service in SERVICES:
                    out = pool.master.ssh(["systemctl", "status", service], check=False)
                    logging.warning("service status: %s", out)
                    out = pool.master.ssh(["grep", "-r", service, "/var/log"], check=False)
                    logging.warning("in logs: %s", out)
                raise

            logging.info("Powering off pool master")
            try:
                # use "poweroff" because "reboot" would cause ARP and
                # SSH to be checked before host is down, and require
                # ssh retries
                pool.master.ssh(["poweroff"])
            except commands.SSHCommandFailed as e:
                # ignore connection closed by reboot
                if e.returncode == 255 and "closed by remote host" in e.stdout:
                    logging.info("sshd closed the connection")
                    pass
                else:
                    raise

            wait_for(host_vm.is_halted, "Wait for host VM halted")

        except Exception as e:
            logging.critical("caught exception %s", e)
            # wait_for(lambda: False, 'Wait "forever"', timeout_secs=100 * 60)
            host_vm.shutdown(force=True)
            raise
        except KeyboardInterrupt:
            logging.warning("keyboard interrupt")
            # wait_for(lambda: False, 'Wait "forever"', timeout_secs=100 * 60)
            host_vm.shutdown(force=True)
            raise

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("admin_iface", ("ipv4dhcp", "ipv4static", "ipv6static", "ipv6ac", "ipv6dhcp"))
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("package_source", ("iso", "net"))
    @pytest.mark.parametrize("system_disk_config", ("disk", "raid1"))
    @pytest.mark.parametrize("machine", ("host1", "host2"))
    @pytest.mark.parametrize("version", (
        "83nightly", "830net",
        "830",
        "82nightly",
        "821.1",
        "81", "80",
        "76", "75",
        "xs8", "ch821.1",
        "xs70",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(
        lambda firmware, version, machine, local_sr, admin_iface, package_source, system_disk_config: [
            dict(vm="vm1",
                 image_test=("TestNested::test_tune_firstboot"
                             f"[None-{firmware}-{version}-{machine}-{system_disk_config}"
                             f"-{package_source}-{local_sr}-{admin_iface}]"))])
    def test_boot_inst(self, create_vms,
                       firmware, version, machine, package_source, system_disk_config, local_sr, admin_iface):
        self._test_firstboot(create_vms, version, admin_iface, machine=machine)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("admin_iface", ("ipv4dhcp", "ipv4static", "ipv6static"))
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("package_source", ("iso", "net"))
    @pytest.mark.parametrize("system_disk_config", ("disk", "raid1"))
    @pytest.mark.parametrize("machine", ("host1", "host2"))
    @pytest.mark.parametrize(("orig_version", "iso_version"), [
        ("83nightly", "83nightly"),
        ("830", "83nightly"),
        ("821.1", "83nightly"),
        ("81", "83nightly"),
        ("80", "83nightly"),
        ("xs8", "83nightly"),
        ("ch821.1", "83nightly"),
        ("830net", "830net"), # FIXME
        ("82nightly", "82nightly"),
        ("821.1", "82nightly"),
        ("821.1", "821.1"),
    ])
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(
        lambda firmware, orig_version, machine, system_disk_config, package_source, local_sr, admin_iface: [dict(
            vm="vm1",
            image_test=(f"TestNested::test_boot_inst[{firmware}-{orig_version}-{machine}-{system_disk_config}"
                        f"-{package_source}-{local_sr}-{admin_iface}]"))])
    @pytest.mark.answerfile(
        lambda system_disks_names, package_source, system_disk_config, iso_version:
        AnswerFile("UPGRADE").top_append(
            {"iso": {"TAG": "source", "type": "local"},
             "net": {"TAG": "source", "type": "url",
                     "CONTENTS": ISO_IMAGES[iso_version]['net-url']},
             }[package_source],
            {"TAG": "existing-installation",
             "CONTENTS": {"disk": system_disks_names[0],
                          "raid1": "md127",
                          }[system_disk_config]},
        ))
    def test_upgrade(self, vm_booted_with_installer, system_disks_names,
                     firmware, orig_version, iso_version, machine, package_source,
                     system_disk_config, local_sr, admin_iface):
        host_vm = vm_booted_with_installer
        installer.monitor_upgrade(ip=host_vm.ip)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("admin_iface", ("ipv4dhcp", "ipv4static", "ipv6static"))
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("package_source", ("iso", "net"))
    @pytest.mark.parametrize("system_disk_config", ("disk", "raid1"))
    @pytest.mark.parametrize("machine", ("host1", "host2"))
    @pytest.mark.parametrize("mode", (
        "83nightly-83nightly",
        "830-83nightly",
        "821.1-83nightly",
        "81-83nightly",
        "80-83nightly",
        "xs8-83nightly",
        "ch821.1-83nightly",
        "830net-830net",
        "82nightly-82nightly",
        "821.1-82nightly",
        "821.1-821.1",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(
        lambda firmware, mode, machine, system_disk_config, package_source, local_sr, admin_iface: [dict(
            vm="vm1",
            image_test=(f"TestNested::test_upgrade[{firmware}-{mode}-{machine}-{system_disk_config}"
                        f"-{package_source}-{local_sr}-{admin_iface}]"))])
    def test_boot_upg(self, create_vms,
                      firmware, mode, machine, package_source, system_disk_config, local_sr, admin_iface):
        self._test_firstboot(create_vms, mode, admin_iface, machine=machine)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("admin_iface", ("ipv4dhcp", "ipv4static", "ipv6static"))
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("package_source", ("iso", "net"))
    @pytest.mark.parametrize("system_disk_config", ("disk", "raid1"))
    @pytest.mark.parametrize(("orig_version", "iso_version"), [
        ("83nightly-83nightly", "83nightly"),
        ("830-83nightly", "83nightly"),
        ("821.1-83nightly", "83nightly"),
        ("81-83nightly", "83nightly"),
        ("80-83nightly", "83nightly"),
        ("xs8-83nightly", "83nightly"),
        ("ch821.1-83nightly", "83nightly"),
        ("830net-830net", "830net"), # FIXME
        ("82nightly-82nightly", "82nightly"),
        ("821.1-82nightly", "82nightly"),
        ("821.1-821.1", "821.1"),
    ])
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(
        lambda firmware, orig_version, local_sr, admin_iface, system_disk_config, package_source: [dict(
            vm="vm1",
            image_test=(f"TestNested::test_boot_upg[{firmware}-{orig_version}-host1-{system_disk_config}"
                        f"-{package_source}-{local_sr}-{admin_iface}]"))])
    @pytest.mark.answerfile(
        lambda system_disks_names, system_disk_config: AnswerFile("RESTORE").top_append(
            {"TAG": "backup-disk",
             "CONTENTS": {"disk": system_disks_names[0],
                          "raid1": "md127",
                          }[system_disk_config]},
        ))
    def test_restore(self, vm_booted_with_installer, system_disks_names,
                     firmware, orig_version, iso_version, package_source,
                     system_disk_config, local_sr, admin_iface):
        host_vm = vm_booted_with_installer
        installer.monitor_restore(ip=host_vm.ip)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("admin_iface", ("ipv4dhcp", "ipv4static", "ipv6static"))
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("package_source", ("iso", "net"))
    @pytest.mark.parametrize("system_disk_config", ("disk", "raid1"))
    @pytest.mark.parametrize("mode", (
        "83nightly-83nightly-83nightly",
        "830-83nightly-83nightly",
        "821.1-83nightly-83nightly",
        "81-83nightly-83nightly",
        "80-83nightly-83nightly",
        "xs8-83nightly-83nightly",
        "ch821.1-83nightly-83nightly",
        "830net-830net-830net", # FIXME
        "82nightly-82nightly-82nightly",
        "821.1-82nightly-82nightly",
        "821.1-821.1-821.1",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(
        lambda firmware, mode, system_disk_config, package_source, local_sr, admin_iface: [dict(
            vm="vm1",
            image_test=(f"TestNested::test_restore[{firmware}-{mode}-{system_disk_config}"
                        f"-{package_source}-{local_sr}-{admin_iface}]"))])
    def test_boot_rst(self, create_vms,
                      firmware, mode, package_source, system_disk_config, local_sr, admin_iface):
        self._test_firstboot(create_vms, mode, admin_iface, is_restore=True)
