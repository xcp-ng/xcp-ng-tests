import logging
import os
import pytest
import time

from lib import commands, pxe
from lib.common import wait_for
from lib.host import Host
from lib.pool import Pool

# make sure all those tests are considered in the dependency graph
pytestmark = pytest.mark.dependency()

@pytest.mark.parametrize("iso_version", (
    "821.1", "83b2",
))
@pytest.mark.parametrize("firmware", ("uefi", "bios"), scope="class")
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
         vifs=[dict(index=0, network_uuid="eabc1038-e40f-2ae5-0781-a3adbec1cae8")], # FIXME
),
                            param_mapping={"firmware": "firmware"})
@pytest.mark.answerfile(lambda firmware: {
        "base": "INSTALL",
        "source": {"type": "local"},
    "primary-disk": {"text": {"uefi": "nvme0n1",
                              "bios": "sda"}[firmware]
                     },
},
                        param_mapping={"firmware": "firmware"})
@pytest.mark.installer_iso(
    lambda version: {
        "821.1": "xcpng-8.2.1-2023",
        "83b2": "xcpng-8.3-beta2",
    }[version],
    param_mapping={"version": "iso_version"})
def test_install(iso_remaster, create_vms, iso_version, firmware):
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
        host_vm.shutdown(force=True)
        raise
    except KeyboardInterrupt:
        logging.warning("keyboard interrupt")
        # wait_for(lambda: False, 'Wait "forever"', timeout_secs=100*60)
        host_vm.shutdown(force=True)
        raise

@pytest.mark.parametrize("mode", (
    "83b2",
    #"83b2-83b2", # 8.3b2 disabled the upgrade from 8.3
    "821.1-83b2",
    "821.1-83b2-83b2",
    "821.1",
    "821.1-821.1",
), scope="class")
@pytest.mark.parametrize("firmware", ("uefi", "bios"), scope="class")
@pytest.mark.continuation_of(
    lambda params, firmware: [dict(vm="vm1",
                                   image_test=("{parent}[{firmware}-{params}]".format(
                                       params=params,
                                       firmware=firmware,
                                       parent={
                                           1: "test_install",
                                           2: "test_upgrade",
                                           3: "test_restore",
                                       }[len(params.split("-"))]
                                   )))],
    param_mapping={"params": "mode", "firmware": "firmware"})
class TestFirstboot:
    @pytest.fixture(autouse=True, scope="class")
    def firstboot_host(self, mode, xcpng_chained_class, create_vms):
        host_vm = create_vms[0]
        vif = host_vm.vifs()[0]
        mac_address = vif.param_get('MAC')
        logging.info("Host VM has MAC %s", mac_address)

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
            tries = 5
            while True:
                try:
                    pool = Pool(host_vm.ip)
                except commands.SSHCommandFailed as e:
                    if "Connection refused" not in e.stdout:
                        raise
                    tries -= 1
                    if tries:
                        logging.warning("retrying connection to pool master")
                        time.sleep(2)
                        continue
                    # retries failed
                    raise
                # it worked!
                break

            # wait for XAPI
            wait_for(pool.master.is_enabled, "Wait for XAPI to be ready", timeout_secs=30 * 60)

            yield pool.master

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
            host_vm.shutdown(force=True)
            raise
        except KeyboardInterrupt:
            logging.warning("keyboard interrupt")
            # wait_for(lambda: False, 'Wait "forever"', timeout_secs=100*60)
            host_vm.shutdown(force=True)
            raise

    # WARNING: tests here should not have any side-effect: then can be
    # run in any order, and an output cache VM will be produced even
    # if only one of the tests gets run (as long as it passes).

    def test_firstboot_services(self, firstboot_host, mode, firmware):
        host = firstboot_host

        # check for firstboot issues
        # FIXME: flaky, must check logs extraction on failure
        for service in ["control-domain-params-init",
                        "network-init",
                        "storage-init",
                        "generate-iscsi-iqn",
                        "create-guest-templates",
                        ]:
            try:
                wait_for(lambda: host.ssh(["test", "-e", f"/var/lib/misc/ran-{service}"],
                                          check=False, simple_output=False,
                                          ).returncode == 0,
                         f"Wait for ran-{service} stamp")
            except TimeoutError:
                logging.warning("investigating lack of ran-%s stamp", service)
                out = host.ssh(["systemctl", "status", service], check=False)
                logging.warning("service status: %s", out)
                out = host.ssh(["grep", "-r", service, "/var/log"], check=False)
                logging.warning("in logs: %s", out)
                raise

    def test_product_version(self, firstboot_host, mode, firmware):
        host = firstboot_host

        # determine version info from `mode`
        expected_dist = "XCP-ng"
        # succession of insta/upg/rst operations
        split_mode = mode.split("-")
        if len(split_mode) == 3:
            # restore: back to 1st installed version
            expected_rel_id = split_mode[0]
        else:
            expected_rel_id = split_mode[-1]
        expected_rel = {
            "821.1": "8.2.1",
            "83b2": "8.3.0",
        }[expected_rel_id]

        lsb_dist = host.ssh(["lsb_release", "-si"])
        lsb_rel = host.ssh(["lsb_release", "-sr"])
        assert (lsb_dist, lsb_rel) == (expected_dist, expected_rel)


@pytest.mark.parametrize(("orig_version", "iso_version"), [
    ("821.1", "821.1"),
    ("821.1", "83b2"),
    #("83b2", "83b2"), # 8.3b2 disabled the upgrade from 8.3
], scope="class")
@pytest.mark.parametrize("firmware", ("uefi", "bios"), scope="class")
@pytest.mark.continuation_of(
    lambda firmware, params: [dict(
        vm="vm1",
        image_test=f"TestFirstboot[{firmware}-{params}]",
        test_anchor="test_firstboot_services")],
    param_mapping={"params": "orig_version", "firmware": "firmware"})
@pytest.mark.installer_iso(
    lambda version: {
        "821.1": "xcpng-8.2.1-2023",
        "83b2": "xcpng-8.3-beta2",
    }[version],
    param_mapping={"version": "iso_version"})
@pytest.mark.answerfile(
    lambda firmware: {
        "base": "UPGRADE",
        "source": {"type": "local"},
        "existing-installation": {"text": {"uefi": "nvme0n1",
                                           "bios": "sda"}[firmware]
                                  },
    },
    param_mapping={"firmware": "firmware"})
def test_upgrade(xcpng_chained_class, iso_remaster, create_vms, orig_version, iso_version, firmware):
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
                                      "'DISPATCH: NEW PHASE: Reading package information'",
                                      "/tmp/install-log"],
                                     check=False, simple_output=False,
                                     ).returncode == 0,
                 "Wait for upgrade preparations to finish",
                 timeout_secs=40*60) # FIXME too big

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

        logging.info("Shutting down Host VM after successful upgrade")
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
        host_vm.shutdown(force=True)
        raise
    except KeyboardInterrupt:
        logging.warning("keyboard interrupt")
        # wait_for(lambda: False, 'Wait "forever"', timeout_secs=100*60)
        host_vm.shutdown(force=True)
        raise

@pytest.mark.parametrize(("orig_version", "iso_version"), [
    ("821.1-83b2", "83b2"),
], scope="class")
@pytest.mark.parametrize("firmware", ("uefi", "bios"), scope="class")
@pytest.mark.continuation_of(
    lambda firmware, params: [dict(
        vm="vm1",
        image_test=f"TestFirstboot[{firmware}-{params}]")],
    param_mapping={"params": "orig_version", "firmware": "firmware"})
@pytest.mark.installer_iso(
    lambda version: {
        "821.1": "xcpng-8.2.1-2023",
        "83b2": "xcpng-8.3-beta2",
    }[version],
    param_mapping={"version": "iso_version"})
@pytest.mark.answerfile(
    lambda firmware: {
        "base": "RESTORE",
        "backup-disk": {"text": {"uefi": "nvme0n1",
                                 "bios": "sda"}[firmware]
                        },
    },
    param_mapping={"firmware": "firmware"})
def test_restore(xcpng_chained_class, firmware, orig_version, iso_version, iso_remaster, create_vms):
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
        host_vm.shutdown(force=True)
        raise
    except KeyboardInterrupt:
        logging.warning("keyboard interrupt")
        # wait_for(lambda: False, 'Wait "forever"', timeout_secs=100*60)
        host_vm.shutdown(force=True)
        raise
