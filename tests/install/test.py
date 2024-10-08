import logging
import pytest

from lib import commands, installer, pxe
from lib.common import wait_for
from lib.installer import AnswerFile
from lib.pool import Pool

from data import NETWORKS
assert "MGMT" in NETWORKS

# Requirements:
# - one XCP-ng host capable of nested virt, with an ISO SR, and a default SR
# - the "small_vm" ISO must have in authorized_keys a SSH key accepted by the
#   ssh server in the installed host version (7.x and earlier reject current
#   ssh-rsa keys, a public ssh-ed25519 key listed in TEST_SSH_PUBKEY should be
#   there)

@pytest.mark.dependency()
class TestNested:
    @pytest.mark.parametrize("iso_version", (
        "83nightly",
        "83rc1", "83b2", "83b1",
        "821.1",
        "81", "80", "76", "75",
        "xs8", "ch821.1",
        "xs70",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.vm_definitions(
        lambda firmware: dict(
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
            vifs=[dict(index=0, network_name=NETWORKS["MGMT"])],
        ))
    @pytest.mark.answerfile(
        lambda install_disk: AnswerFile("INSTALL").top_append(
            {"TAG": "source", "type": "local"},
            {"TAG": "primary-disk", "CONTENTS": install_disk},
        ))
    def test_install(self, vm_booted_with_installer, install_disk,
                     firmware, iso_version):
        host_vm = vm_booted_with_installer
        installer.monitor_install(ip=host_vm.ip)

    def _test_firstboot(self, create_vms, mode, is_restore=False):
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
            "xs70": "7.0.0-125380c",
            "ch821.1": "8.2.1",
            "xs8": "8.4.0",
            "75": "7.5.0",
            "76": "7.6.0",
            "80": "8.0.0",
            "81": "8.1.0",
            "821.1": "8.2.1",
            "83b1": "8.3.0",
            "83b2": "8.3.0",
            "83rc1": "8.3.0",
            "83nightly": "8.3.0",
        }[expected_rel_id]

        # determine version info from `mode`
        if expected_rel_id.startswith("xs"):
            expected_dist = "XenServer"
        elif expected_rel_id.startswith("ch"):
            expected_dist = "CitrixHypervisor"
        else:
            expected_dist = "XCP-ng"

        try:
            # FIXME: evict MAC from ARP cache first?
            host_vm.start()
            wait_for(host_vm.is_running, "Wait for host VM running")

            # catch host-vm IP address
            wait_for(lambda: pxe.arp_addresses_for(mac_address),
                     "Wait for DHCP server to see Host VM in ARP tables",
                     timeout_secs=10 * 60)
            ips = pxe.arp_addresses_for(mac_address)
            logging.info("Host VM has IPs %s", ips)
            assert len(ips) == 1
            host_vm.ip = ips[0]

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
    @pytest.mark.parametrize("machine", ("host1", "host2"))
    @pytest.mark.parametrize("version", (
        "83nightly",
        "83rc1", "83b2", "83b1",
        "821.1",
        "81", "80",
        "76", "75",
        "xs8", "ch821.1",
        "xs70",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(
        lambda firmware, version: [
            dict(vm="vm1", image_test=f"TestNested::test_install[{firmware}-{version}]")])
    def test_boot_inst(self, create_vms,
                       firmware, version, machine):
        self._test_firstboot(create_vms, version)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("machine", ("host1", "host2"))
    @pytest.mark.parametrize("mode", (
        "83nightly-83nightly",
        "83rc1-83nightly",
        "83b2-83nightly",
        "83b1-83nightly",
        "821.1-83nightly",
        "81-83nightly",
        "80-83nightly",
        "xs8-83nightly",
        "ch821.1-83nightly",
        "821.1-821.1",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(
        lambda firmware, mode, machine: [dict(
            vm="vm1",
            image_test=(f"TestNested::test_upgrade[{firmware}-{mode}-{machine}]"))])
    def test_boot_upg(self, create_vms,
                      firmware, mode, machine):
        self._test_firstboot(create_vms, mode)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("mode", (
        "83nightly-83nightly-83nightly",
        "83rc1-83nightly-83nightly",
        "83b2-83nightly-83nightly",
        "83b1-83nightly-83nightly",
        "821.1-83nightly-83nightly",
        "81-83nightly-83nightly",
        "80-83nightly-83nightly",
        "xs8-83nightly-83nightly",
        "ch821.1-83nightly-83nightly",
        "821.1-821.1-821.1",
    ))
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(
        lambda firmware, mode: [dict(
            vm="vm1",
            image_test=(f"TestNested::test_restore[{firmware}-{mode}]"))])
    def test_boot_rst(self, create_vms,
                      firmware, mode):
        self._test_firstboot(create_vms, mode, is_restore=True)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize("machine", ("host1", "host2"))
    @pytest.mark.parametrize(("orig_version", "iso_version"), [
        ("83nightly", "83nightly"),
        ("83rc1", "83nightly"),
        ("83b2", "83nightly"),
        ("83b1", "83nightly"),
        ("821.1", "83nightly"),
        ("81", "83nightly"),
        ("80", "83nightly"),
        ("xs8", "83nightly"),
        ("ch821.1", "83nightly"),
        ("821.1", "821.1"),
    ])
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(
        lambda firmware, orig_version, machine: [dict(
            vm="vm1",
            image_test=f"TestNested::test_boot_inst[{firmware}-{orig_version}-{machine}]")])
    @pytest.mark.answerfile(
        lambda install_disk: AnswerFile("UPGRADE").top_append(
            {"TAG": "source", "type": "local"},
            {"TAG": "existing-installation",
             "CONTENTS": install_disk},
        ))
    def test_upgrade(self, vm_booted_with_installer, install_disk,
                     firmware, orig_version, iso_version, machine):
        host_vm = vm_booted_with_installer
        installer.monitor_upgrade(ip=host_vm.ip)

    @pytest.mark.usefixtures("xcpng_chained")
    @pytest.mark.parametrize(("orig_version", "iso_version"), [
        ("83nightly-83nightly", "83nightly"),
        ("83rc1-83nightly", "83nightly"),
        ("83b2-83nightly", "83nightly"),
        ("83b1-83nightly", "83nightly"),
        ("821.1-83nightly", "83nightly"),
        ("81-83nightly", "83nightly"),
        ("80-83nightly", "83nightly"),
        ("xs8-83nightly", "83nightly"),
        ("ch821.1-83nightly", "83nightly"),
        ("821.1-821.1", "821.1"),
    ])
    @pytest.mark.parametrize("firmware", ("uefi", "bios"))
    @pytest.mark.continuation_of(
        lambda firmware, orig_version: [dict(
            vm="vm1",
            image_test=f"TestNested::test_boot_upg[{firmware}-{orig_version}-host1]")])
    @pytest.mark.answerfile(
        lambda install_disk: AnswerFile("RESTORE").top_append(
            {"TAG": "backup-disk",
             "CONTENTS": install_disk},
        ))
    def test_restore(self, vm_booted_with_installer, install_disk,
                     firmware, orig_version, iso_version):
        host_vm = vm_booted_with_installer
        installer.monitor_restore(ip=host_vm.ip)
