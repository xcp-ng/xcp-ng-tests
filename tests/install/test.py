import logging
import os
import pytest
import time

from lib import commands, pxe
from lib.common import wait_for
from lib.host import Host
from lib.pool import Pool

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
