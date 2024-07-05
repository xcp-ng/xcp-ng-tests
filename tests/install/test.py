import logging
import pytest
import time

from lib import commands, installer, pxe
from lib.common import safe_split, wait_for
from lib.installer import AnswerFile
from lib.pif import PIF
from lib.pool import Pool
from lib.vdi import VDI

from data import HOSTS_IP_CONFIG, ISO_IMAGES, NETWORKS
assert "MGMT" in NETWORKS
assert "HOSTS" in HOSTS_IP_CONFIG

@pytest.mark.dependency()
class TestNested:
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("source_type", ("iso", "net"))
    @pytest.mark.parametrize("iso_version", (
        "83rc1", "83rc1net", "83b2",
        "821.1",
        "81", "80", "76", "75",
        "xs8", "ch821.1",
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
        cd_vbd=dict(device="xvdd", userdevice="3"),
        vifs=[dict(index=0, network_uuid=NETWORKS["MGMT"])],
    ),
                                param_mapping={"firmware": "firmware"})
    @pytest.mark.installer_iso(
        lambda iso_version: iso_version,
        gen_unique_uuid=True,
        param_mapping={"iso_version": "iso_version"})
    @pytest.mark.answerfile(lambda firmware, local_sr, source_type, version: AnswerFile("INSTALL") \
                            .top_setattr({} if local_sr == "nosr" else {"sr-type": local_sr}) \
                            .top_append(
                                {'iso': {"TAG": "source", "type": "local"},
                                 'net': {"TAG": "source", "type": "url",
                                         # FIXME evaluation requires 'net-url' for every ISO
                                         "CONTENTS": ISO_IMAGES[version]['net-url']}}[source_type],
                                {"TAG": "primary-disk",
                                 "guest-storage": "no" if local_sr == "nosr" else "yes",
                                 "CONTENTS": {"uefi": "nvme0n1", "bios": "sda"}[firmware]},
                            ),
                            param_mapping={"firmware": "firmware", "local_sr": "local_sr",
                                           "source_type": "source_type", "version": "iso_version",
                                           })
    def test_install(self, create_vms, iso_remaster,
                     firmware, iso_version, source_type, local_sr):
        assert len(create_vms) == 1
        installer.perform_install(iso=iso_remaster, host_vm=create_vms[0])

    @pytest.fixture
    @staticmethod
    def helper_vm_with_plugged_disk(imported_vm, create_vms):
        helper_vm = imported_vm
        host_vm, = create_vms

        helper_vm.start()
        helper_vm.wait_for_vm_running_and_ssh_up()

        all_vdis = [VDI(uuid, host=host_vm.host) for uuid in host_vm.vdi_uuids()]
        disk_vdis = [vdi for vdi in all_vdis if not vdi.readonly()]
        vdi, = disk_vdis

        vbd = helper_vm.create_vbd("1", vdi.uuid)
        try:
            vbd.plug()

            yield helper_vm

        finally:
            vbd.unplug()
            vbd.destroy()

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("source_type", ("iso", "net"))
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
    @pytest.mark.continuation_of(lambda version, firmware, local_sr, source_type: [dict(
        vm="vm1",
        image_test=f"TestNested::test_install[{firmware}-{version}-{source_type}-{local_sr}]")],
                                 param_mapping={"version": "version", "firmware": "firmware",
                                                "local_sr": "local_sr", "source_type": "source_type"})
    @pytest.mark.small_vm
    def test_tune_firstboot(self, create_vms, helper_vm_with_plugged_disk,
                            firmware, version, machine, local_sr, source_type):
        helper_vm = helper_vm_with_plugged_disk

        helper_vm.ssh(["mount /dev/xvdb1 /mnt"])
        try:
            # hostname
            logging.info("Setting hostname to %r", machine)
            helper_vm.ssh(["echo > /mnt/etc/hostname", machine])
            # management IP
            if machine in HOSTS_IP_CONFIG['HOSTS']:
                ip = HOSTS_IP_CONFIG['HOSTS'][machine]
                logging.info("Changing IP to %s", ip)

                helper_vm.ssh([f"sed -i s/^IP=.*/IP='{ip}'/",
                               "/mnt/etc/firstboot.d/data/management.conf"])
        finally:
            helper_vm.ssh(["umount /dev/xvdb1"])

    def _test_firstboot(self, host_vm, mode, *, machine='DEFAULT'):
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
            "83rc1net": "8.3.0",
        }[expected_rel_id]

        try:
            host_vm.start()
            wait_for(host_vm.is_running, "Wait for host VM running")

            host_vm.ip = HOSTS_IP_CONFIG['HOSTS'].get(machine,
                                                      HOSTS_IP_CONFIG['HOSTS']['DEFAULT'])
            logging.info("Expecting host VM to have IP %s", host_vm.ip)

            wait_for(
                lambda: commands.local_cmd(
                    ["nc", "-zw5", host_vm.ip, "22"], check=False).returncode == 0,
                "Wait for ssh back up on Host VM", retry_delay_secs=5, timeout_secs=4 * 60)

            logging.info("Checking installed version")
            lsb_dist = commands.ssh(host_vm.ip, ["lsb_release", "-si"])
            lsb_rel = commands.ssh(host_vm.ip, ["lsb_release", "-sr"])
            assert (lsb_dist, lsb_rel) == (expected_dist, expected_rel)

            lsb_rel_tuple = tuple(int(x) for x in lsb_rel.split("."))

            # wait for XAPI startup to be done, which avoids:
            # - waiting for XAPI to start listening to its socket
            # - waiting for host and pool objects to be populated after install
            wait_for(lambda: commands.ssh(host_vm.ip, ['xapi-wait-init-complete', '60'],
                                          check=False, simple_output=False).returncode == 0,
                     "Wait for XAPI init to be complete",
                     timeout_secs=30 * 60)
            # FIXME: after this all wait_for should be instant - replace with immediate tests?

            # pool master must be reachable here
            pool = Pool(host_vm.ip)
            logging.info("Host uuid: %s", pool.master.uuid)

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
                logging.warning("investigating lack of %s service stamp", stamp)
                for service in SERVICES:
                    out = pool.master.ssh(["systemctl", "status", service], check=False)
                    logging.warning("service status: %s", out)
                    out = pool.master.ssh(["grep", "-r", service, "/var/log"], check=False)
                    logging.warning("in logs: %s", out)
                raise

            if lsb_rel_tuple >= (8, 3):
                # Those certs take time to get generated in install case,
                # so make sure they are.  On upgrade they must be
                # preserved (fails on 8.3.0-rc1).
                for certfile in (
                        "/etc/stunnel/xapi-stunnel-ca-bundle.pem",
                        "/etc/stunnel/xapi-pool-ca-bundle.pem",
                        "/etc/xensource/xapi-pool-tls.pem",
                ):
                    wait_for(lambda: pool.master.ssh(["test", "-e", certfile],
                                                     check=False, simple_output=False,
                                                     ).returncode == 0,
                             f"Wait for {certfile} certificate file")

            #wait_for(lambda: False, 'Wait "forever"', timeout_secs=100*60)
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
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("source_type", ("iso", "net"))
    @pytest.mark.parametrize("machine", ("host1", "host2"))
    @pytest.mark.parametrize("version", (
        "83rc1", "83rc1net",
        "83b2",
        "821.1",
        "81", "80",
        "76", "75",
        "ch821.1", "xs8",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(lambda version, firmware, machine, local_sr, source_type: [dict(
        vm="vm1",
        image_test=f"TestNested::test_tune_firstboot[None-{firmware}-{version}-{machine}-{source_type}-{local_sr}]")],
                                 param_mapping={"version": "version", "firmware": "firmware",
                                                "machine": "machine", "local_sr": "local_sr",
                                                "source_type": "source_type"})
    def test_firstboot_install(self, create_vms,
                               firmware, version, machine, source_type, local_sr):
        host_vm = create_vms[0]

        self._test_firstboot(host_vm, version, machine=machine)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("source_type", ("iso", "net"))
    @pytest.mark.parametrize("mode", (
        "83rc1-83rc1", "83rc1-83rc1-83rc1",
        "83rc1net-83rc1net", "83rc1net-83rc1net-83rc1net",
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
        image_test=(f"TestNested::{{}}[{firmware}-{params}-{source_type}-{local_sr}]".format(
            {
                2: "test_upgrade",
                3: "test_restore",
            }[len(params.split("-"))]
        )))],
                                 param_mapping={"params": "mode", "firmware": "firmware",
                                                "local_sr": "local_sr", "source_type": "source_type"})
    def test_firstboot_noninst(self, create_vms,
                               firmware, mode, source_type, local_sr):
        host_vm = create_vms[0]
        self._test_firstboot(host_vm, mode)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("source_type", ("iso", "net"))
    @pytest.mark.parametrize(("orig_version", "iso_version"), [
        ("83rc1", "83rc1"),
        ("83rc1net", "83rc1net"),
        ("83b2", "83rc1"),
        ("821.1", "83rc1"),
        ("81", "83rc1"),
        ("80", "83rc1"),
        ("76", "83rc1"),
        ("75", "83rc1"),
        ("ch821.1", "83rc1"),
        ("821.1", "821.1"),
    ])
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(lambda firmware, params, local_sr, source_type: [dict(
        vm="vm1",
        image_test=f"TestNested::test_firstboot_install[{firmware}-{params}-host1-{source_type}-{local_sr}]")],
                                 param_mapping={"params": "orig_version", "firmware": "firmware",
                                                "local_sr": "local_sr", "source_type": "source_type"})
    @pytest.mark.installer_iso(
        lambda iso_version: iso_version,
        param_mapping={"iso_version": "iso_version"})
    @pytest.mark.answerfile(
        lambda firmware: AnswerFile("UPGRADE").top_append(
            {"TAG": "source", "type": "local"},
            {"TAG": "existing-installation",
             "CONTENTS": {"uefi": "nvme0n1", "bios": "sda"}[firmware]},
        ),
        param_mapping={"firmware": "firmware"})
    def test_upgrade(self, create_vms, iso_remaster,
                     firmware, orig_version, iso_version, source_type, local_sr):
        installer.perform_upgrade(iso=iso_remaster, host_vm=create_vms[0])

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("local_sr", ("nosr", "ext", "lvm"))
    @pytest.mark.parametrize("source_type", ("iso", "net"))
    @pytest.mark.parametrize(("orig_version", "iso_version"), [
        ("83rc1-83rc1", "83rc1"),
        ("83rc1net-83rc1net", "83rc1net"),
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
        image_test=f"TestNested::test_firstboot_noninst[{firmware}-{params}-{source_type}-{local_sr}]")],
                                 param_mapping={"params": "orig_version", "firmware": "firmware",
                                                "local_sr": "local_sr", "source_type": "source_type"})
    @pytest.mark.installer_iso(
        lambda iso_version: iso_version,
        param_mapping={"iso_version": "iso_version"})
    @pytest.mark.answerfile(lambda firmware: AnswerFile("RESTORE").top_append(
        {"TAG": "backup-disk",
         "CONTENTS": {"uefi": "nvme0n1", "bios": "sda"}[firmware]},
    ),
                            param_mapping={"firmware": "firmware"})
    def test_restore(self, create_vms, iso_remaster,
                     firmware, orig_version, iso_version, source_type, local_sr):
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
