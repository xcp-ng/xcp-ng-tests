import logging
import os
import pytest
import time

from lib import commands, installer, pxe
from lib.common import safe_split, wait_for
from lib.pif import PIF
from lib.pool import Pool

from data import NETWORKS
assert "MGMT" in NETWORKS

@pytest.mark.dependency()
class TestNested:
    @pytest.mark.parametrize("source_type", ("local", "netinstall"))
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("iso_version", (
        "75", "76", "80", "81",
        "ch821.1", "xs8",
        "821.1", "83b2", "83rc1",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.vm_definitions(lambda firmware: dict(
        name="vm1",
        template="Other install media",
        params=(
            # dict(param_name="", value=""),
            dict(param_name="memory-static-max", value="4GiB"),
            dict(param_name="memory-dynamic-max", value="4GiB"),
            dict(param_name="memory-dynamic-min", value="4GiB"),
            dict(param_name="platform", key="exp-nested-hvm", value="true"), # FIXME < 8.3 host?
            dict(param_name="HVM-boot-params", key="order", value="dc"),
        ) + {
            "uefi": (
                dict(param_name="HVM-boot-params", key="firmware", value="uefi"),
                dict(param_name="platform", key="device-model", value="qemu-upstream-uefi"),
            ),
            "bios": (),
        }[firmware],
        vdis=[dict(name="vm1 system disk", size="100GiB", device="xvda", userdevice="0")],
        vifs=[dict(index=0, network_uuid=NETWORKS["MGMT"])],
    ),
                                param_mapping={"firmware": "firmware"})
    @pytest.mark.installer_iso(
        lambda version, source_type: ({
            "75": "xcpng-7.5",
            "76": "xcpng-7.6",
            "80": "xcpng-8.0",
            "81": "xcpng-8.1",
            "821.1": "xcpng-8.2.1-2023",
            "83b2": "xcpng-8.3-beta2",
            "83rc1": "xcpng-8.3-rc1",
            "ch821.1": "ch-8.2.1-23",
            "xs8": "xs8-2024-03",
        }[version], source_type),
        gen_unique_uuid=True,
        param_mapping={"version": "iso_version", "source_type": "source_type"})
    @pytest.mark.answerfile(
        lambda firmware, local_sr: {
            "base": "INSTALL",
            # FIXME this overrides part of base data
            "root": {"tag": "installation"} if local_sr == "nosr" else {
                "tag": "installation", "sr-type": local_sr,
            },
            "source": {"type": "local"},
            "primary-disk": {"text": {"uefi": "nvme0n1",
                                      "bios": "sda"}[firmware],
            "guest-storage": "no" if local_sr == "nosr" else "yes",
            },
        },
        param_mapping={"firmware": "firmware", "local_sr": "local_sr"})
    def test_install(self, firmware, create_vms, iso_remaster, iso_version, local_sr, source_type):
        assert len(create_vms) == 1
        host_vm = create_vms[0]
        # FIXME should be part of vm def
        host_vm.create_cd_vbd(device="xvdd", userdevice="3")

        vif = host_vm.vifs()[0]
        mac_address = vif.param_get('MAC')
        logging.info("Host VM has MAC %s", mac_address)

        host_vm.insert_cd(iso_remaster)

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

            host_vm.ssh(["ls"])
            logging.debug("ssh works")

            # wait for "yum install" phase to finish
            wait_for(lambda: host_vm.ssh(["grep",
                                          "'DISPATCH: NEW PHASE: Completing installation'",
                                          "/tmp/install-log"],
                                         check=False, simple_output=False,
                                         ).returncode == 0,
                     "Wait for rpm installation to succeed",
                     timeout_secs=40*60) # FIXME too big

            # wait for install to finish
            wait_for(lambda: host_vm.ssh(["grep",
                                          "'The installation completed successfully'",
                                          "/tmp/install-log"],
                                         check=False, simple_output=False,
                                         ).returncode == 0,
                     "Wait for system installation to succeed",
                     timeout_secs=40*60) # FIXME too big

            wait_for(lambda: host_vm.ssh(["ps a|grep '[0-9]. python /opt/xensource/installer/init'"],
                                         check=False, simple_output=False,
                                         ).returncode == 1,
                     "Wait for installer to terminate")

            logging.info("Shutting down Host VM after successful installation")
            try:
                host_vm.ssh(["poweroff"])
            except commands.SSHCommandFailed as e:
                # ignore connection closed by reboot
                if e.returncode == 255 and "closed by remote host" in e.stdout:
                    logging.info("sshd closed the connection")
                    pass
                else:
                    raise
            wait_for(host_vm.is_halted, "Wait for host VM halted")
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


    def _test_firstboot(self, create_vms, mode, *, set_hostname=None):
        host_vm = create_vms[0]
        vif = host_vm.vifs()[0]
        mac_address = vif.param_get('MAC')
        logging.info("Host VM has MAC %s", mac_address)

        # determine version info from `mode`
        if mode.startswith("xs"):
            expected_dist = "XenServer"
        elif mode.startswith("ch"):
            expected_dist = "CitrixHypervisor"
        else:
            expected_dist = "XCP-ng"
        # succession of insta/upg/rst operations
        split_mode = mode.split("-")
        if len(split_mode) == 3:
            # restore: back to 1st installed version
            expected_rel_id = split_mode[0]
        else:
            expected_rel_id = split_mode[-1]
        expected_rel = {
            "ch821.1": "8.2.1",
            "xs8": "8.4.0",
            "75": "7.5.0",
            "76": "7.6.0",
            "80": "8.0.0",
            "81": "8.1.0",
            "821.1": "8.2.1",
            "83b2": "8.3.0",
            "83rc1": "8.3.0",
        }[expected_rel_id]

        try:
            # FIXME: evict MAC from ARP cache first?
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

            wait_for(lambda: not os.system(f"nc -zw5 {host_vm.ip} 22"),
                     "Wait for ssh back up on Host VM", retry_delay_secs=5)

            # FIXME "xe host-list" in there can fail with various
            # errors until the XAPI DB is initialized enough for
            # Pool.__init__(), which assumes the master XAPI is up and
            # running.

            # pool master must be reachable here
            # FIXME: not sure why we seem to need this, while port 22 has been seen open
            tries = 7
            while True:
                try:
                    pool = Pool(host_vm.ip)
                except commands.SSHCommandFailed as e:
                    if "Connection refused" not in e.stdout:
                        raise
                    tries -= 1
                    if tries:
                        logging.warning("retrying connection to pool master")
                        time.sleep(3)
                        continue
                    # retries failed
                    raise
                # it worked!
                break

            logging.info("Host uuid: %s", pool.master.uuid)

            logging.info("Checking installed version")
            lsb_dist = pool.master.ssh(["lsb_release", "-si"])
            lsb_rel = pool.master.ssh(["lsb_release", "-sr"])
            assert (lsb_dist, lsb_rel) == (expected_dist, expected_rel)

            # wait for XAPI
            wait_for(pool.master.is_enabled, "Wait for XAPI to be ready", timeout_secs=30 * 60)

            if lsb_rel in ["8.2.1", "8.3.0", "8.4.0"]:
                SERVICES =["control-domain-params-init",
                            "network-init",
                            "storage-init",
                            "generate-iscsi-iqn",
                            "create-guest-templates",
                            ]
                STAMPS_DIR = "/var/lib/misc"
                STAMPS = [f"ran-{service}" for service in SERVICES]
            elif lsb_rel in ["7.5.0", "7.6.0", "8.0.0", "8.1.0"]:
                SERVICES = ["xs-firstboot"]
                STAMPS_DIR = "/etc/firstboot.d/state"
                STAMPS = [
                    "05-prepare-networking",
                    "10-prepare-storage",
                    "15-set-default-storage",
                    "20-udev-storage",
                    "25-multipath",
                    "40-generate-iscsi-iqn",
                    "50-prepare-control-domain-params",
                    "60-import-keys",
                    "60-upgrade-likewise-to-pbis",
                    "62-create-guest-templates",
                    "90-flush-pool-db",
                    "95-legacy-logrotate",
                    "99-remove-firstboot-flag",
                ]
                if lsb_rel in ["8.0.0", "8.1.0"]:
                    STAMPS += [
                        "80-common-criteria",
                    ]
            # check for firstboot issues
            # FIXME: flaky, must check logs extraction on failure
            try:
                for stamp in STAMPS:
                    wait_for(lambda: pool.master.ssh(["test", "-e", f"{STAMPS_DIR}/{stamp}"],
                                                     check=False, simple_output=False,
                                                     ).returncode == 0,
                             f"Wait for {stamp} stamp")
            except TimeoutError:
                logging.warning("investigating lack of {stamp} service stamp")
                for service in SERVICES:
                    out = pool.master.ssh(["systemctl", "status", service], check=False)
                    logging.warning("service status: %s", out)
                    out = pool.master.ssh(["grep", "-r", service, "/var/log"], check=False)
                    logging.warning("in logs: %s", out)

            if set_hostname:
                pool.master.param_set("name-label", set_hostname)
                pool.master.xe("host-set-hostname-live", {"host-uuid": pool.master.uuid,
                                                          "host-name": set_hostname})
                # mode IP to static - FIXME not really in a good place but hey
                # FIXME management_network() -> PIF -> filter on host ?
                mgmt_pif_uuids = safe_split(pool.master.xe("pif-list",
                                                           {"host-uuid": pool.master.uuid},
                                                           minimal=True))
                assert len(mgmt_pif_uuids) == 1
                mgmt_pif = PIF(uuid=mgmt_pif_uuids[0], host=pool.master)
                ip = mgmt_pif.param_get("IP")
                netmask = mgmt_pif.param_get("netmask")
                gateway = mgmt_pif.param_get("gateway")
                dns = mgmt_pif.param_get("DNS")
                pool.master.xe("pif-reconfigure-ip", {"uuid": mgmt_pif.uuid,
                                                      "mode": "static",
                                                      "IP": ip, "netmask": netmask,
                                                      "gateway": gateway, "DNS": dns})

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
            # wait_for(lambda: False, 'Wait "forever"', timeout_secs=100*60)
            #host_vm.shutdown(force=True)
            raise
        except KeyboardInterrupt:
            logging.warning("keyboard interrupt")
            # wait_for(lambda: False, 'Wait "forever"', timeout_secs=100*60)
            #host_vm.shutdown(force=True)
            raise

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("source_type", ("local", "netinstall"))
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("machine", ("host1", "host2"))
    @pytest.mark.parametrize("version", (
        "83rc1",
        "83b2",
        "821.1",
        "81", "80",
        "76", "75",
        "ch821.1", "xs8",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(lambda version, firmware, machine, local_sr, source_type: [dict(
        vm="vm1",
        image_test=f"TestNested::test_install[{firmware}-{version}-{local_sr}-{source_type}]")],
                                 param_mapping={"version": "version", "firmware": "firmware",
                                                "machine": "machine", "local_sr": "local_sr",
                                                "source_type": "source_type"})
    def test_firstboot_install(self, firmware, create_vms, version, machine, local_sr, source_type):
        self._test_firstboot(create_vms, version, set_hostname=machine)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("source_type", ("local", "netinstall"))
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("mode", (
        "83rc1-83rc1", "83rc1-83rc1-83rc1",
        "83b2-83rc1",
        "821.1-83rc1",
        "821.1-83rc1-83rc1",
        "81-83rc1", "81-83rc1-83rc1",
        "80-83rc1", "80-83rc1-83rc1",
        "76-83rc1", "76-83rc1-83rc1",
        "75-83rc1", "75-83rc1-83rc1",
        "ch821.1-83rc1",
        "ch821.1-83rc1-83rc1",
        "821.1-821.1",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(lambda params, firmware, local_sr, source_type: [dict(
        vm="vm1",
        image_test=(f"TestNested::{{}}[{firmware}-{params}-{local_sr}-{source_type}]".format(
            {
                2: "test_upgrade",
                3: "test_restore",
            }[len(params.split("-"))]
        )))],
                                 param_mapping={"params": "mode", "firmware": "firmware",
                                                "local_sr": "local_sr", "source_type": "source_type"})
    def test_firstboot_noninst(self, firmware, create_vms, mode, local_sr, source_type):
        self._test_firstboot(create_vms, mode)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("source_type", ("local", "netinstall"))
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize(("orig_version", "iso_version"), [
        ("821.1", "821.1"),
        ("75", "83rc1"),
        ("76", "83rc1"),
        ("80", "83rc1"),
        ("81", "83rc1"),
        ("ch821.1", "83rc1"),
        ("821.1", "83rc1"),
        ("83b2", "83rc1"),
        ("83rc1", "83rc1"),
    ])
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(lambda firmware, params, local_sr, source_type: [dict(
        vm="vm1",
        image_test=f"TestNested::test_firstboot_install[{firmware}-{params}-host1-{local_sr}-{source_type}]")],
                                 param_mapping={"params": "orig_version", "firmware": "firmware",
                                                "local_sr": "local_sr", "source_type": "source_type"})
    @pytest.mark.installer_iso(
        lambda version, source_type: ({
            "821.1": "xcpng-8.2.1-2023",
            "83rc1": "xcpng-8.3-rc1",
        }[version], source_type),
        param_mapping={"version": "iso_version", "source_type": "source_type"})
    @pytest.mark.answerfile(lambda firmware: {
            "base": "UPGRADE",
            "source": {"type": "local"},
            "existing-installation": {"text": {"uefi": "nvme0n1",
                                               "bios": "sda"}[firmware]
                                      },
        },
                            param_mapping={"firmware": "firmware"})
    def test_upgrade(self, firmware, create_vms, iso_remaster, orig_version, iso_version, local_sr, source_type):
        installer.perform_upgrade(iso=iso_remaster, host_vm=create_vms[0])

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("source_type", ("local", "netinstall"))
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize(("orig_version", "iso_version"), [
        ("83rc1-83rc1", "83rc1"),
        ("821.1-83rc1", "83rc1"),
        ("75-83rc1", "83rc1"),
        ("76-83rc1", "83rc1"),
        ("80-83rc1", "83rc1"),
        ("81-83rc1", "83rc1"),
        ("ch821.1-83rc1", "83rc1"),
    ])
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(lambda firmware, params, local_sr, source_type: [dict(
        vm="vm1",
        image_test=f"TestNested::test_firstboot_noninst[{firmware}-{params}-{local_sr}-{source_type}]")],
                                 param_mapping={"params": "orig_version", "firmware": "firmware",
                                                "local_sr": "local_sr", "source_type": "source_type"})
    @pytest.mark.installer_iso(
        lambda version, source_type: ({
            "821.1": "xcpng-8.2.1-2023",
            "83rc1": "xcpng-8.3-rc1",
        }[version], source_type),
        param_mapping={"version": "iso_version", "source_type": "source_type"})
    @pytest.mark.answerfile(lambda firmware: {
        "base": "RESTORE",
        "backup-disk": {"text": {"uefi": "nvme0n1",
                                 "bios": "sda"}[firmware]
                        },
    },
                            param_mapping={"firmware": "firmware"})
    def test_restore(self, firmware, orig_version, iso_version, create_vms, iso_remaster, local_sr, source_type):
        host_vm = create_vms[0]
        vif = host_vm.vifs()[0]
        mac_address = vif.param_get('MAC')
        logging.info("Host VM has MAC %s", mac_address)

        host_vm.insert_cd(iso_remaster)

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

            # wait for "yum install" phase to start
            wait_for(lambda: host_vm.ssh(["grep",
                                          "'Restoring backup'",
                                          "/tmp/install-log"],
                                         check=False, simple_output=False,
                                         ).returncode == 0,
                     "Wait for data restoration to start",
                     timeout_secs=40*60) # FIXME too big

            # wait for "yum install" phase to finish
            wait_for(lambda: host_vm.ssh(["grep",
                                          "'Data restoration complete.  About to re-install bootloader.'",
                                          "/tmp/install-log"],
                                         check=False, simple_output=False,
                                         ).returncode == 0,
                     "Wait for data restoration to complete",
                     timeout_secs=40*60) # FIXME too big

            # The installer will not terminate in restore mode, it
            # requires human interaction and does not even log it, so
            # wait for last known action log (tested with 8.3b2)
            wait_for(lambda: host_vm.ssh(["grep",
                                          "'ran .*swaplabel.*rc 0'",
                                          "/tmp/install-log"],
                                         check=False, simple_output=False,
                                         ).returncode == 0,
                     "Wait for installer to hopefully finish",
                     timeout_secs=40*60) # FIXME too big

            # "wait a bit to be extra sure".  Yuck.
            time.sleep(30)

            logging.info("Shutting down Host VM after successful restore")
            try:
                host_vm.ssh(["poweroff"])
            except commands.SSHCommandFailed as e:
                # ignore connection closed by reboot
                if e.returncode == 255 and "closed by remote host" in e.stdout:
                    logging.info("sshd closed the connection")
                    pass
                else:
                    raise
            wait_for(host_vm.is_halted, "Wait for host VM halted")
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
