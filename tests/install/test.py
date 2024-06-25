import logging
import os
import pytest
import time

from lib import commands, pxe
from lib.common import wait_for
from lib.host import Host
from lib.pool import Pool

@pytest.mark.dependency()
class TestNested:
    @pytest.mark.vm_definitions(
        dict(name="vm1",
             template="Other install media",
             params=(
                 # dict(param_name="", value=""),
                 dict(param_name="memory-static-max", value="4GiB"),
                 dict(param_name="memory-dynamic-max", value="4GiB"),
                 dict(param_name="memory-dynamic-min", value="4GiB"),
                 dict(param_name="platform", key="exp-nested-hvm", value="true"), # FIXME < 8.3 host?
                 dict(param_name="HVM-boot-params", key="firmware", value="uefi"),
                 dict(param_name="HVM-boot-params", key="order", value="dc"),
                 dict(param_name="platform", key="device-model", value="qemu-upstream-uefi"),
             ),
             vdis=[dict(name="vm1 system disk", size="100GiB", device="xvda", userdevice="0")],
             vifs=[dict(index=0, network_uuid="eabc1038-e40f-2ae5-0781-a3adbec1cae8")], # FIXME
             ))
    @pytest.mark.answerfile(
        {
            "base": "INSTALL",
            "source": {"type": "local"},
            "primary-disk": {"text": "nvme0n1"},
        })
    @pytest.mark.installer_iso("xcpng-8.2.1-2023")
    def test_install(self, iso_remaster, create_vms):
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


    @pytest.mark.dependency(depends=["TestNested::test_install"])
    @pytest.mark.vm_definitions(
        dict(name="vm1", image_test="TestNested::test_install"))
    def test_firstboot(self, create_vms):
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

            # check for firstboot issues
            # FIXME: flaky, must check logs extraction on failure
            for service in ["control-domain-params-init",
                            "network-init",
                            "storage-init",
                            "generate-iscsi-iqn",
                            "create-guest-templates",
                            ]:
                try:
                    wait_for(lambda: pool.master.ssh(["test", "-e", f"/var/lib/misc/ran-{service}"],
                                                     check=False, simple_output=False,
                                                     ).returncode == 0,
                             f"Wait for ran-{service} stamp")
                except TimeoutError:
                    logging.warning("investigating lack of ran-{service} stamp")
                    out = pool.master.ssh(["systemctl", "status", service], check=False)
                    logging.warning("service status: %s", out)
                    out = pool.master.ssh(["grep", "-r", service, "/var/log"], check=False)
                    logging.warning("in logs: %s", out)

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