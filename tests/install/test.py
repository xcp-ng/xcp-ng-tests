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

@pytest.mark.dependency()
class TestNested:
    @pytest.mark.iso_version("821.1")
    @pytest.mark.vm_definitions(
        dict(
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
                dict(param_name="HVM-boot-params", key="firmware", value="uefi"),
                dict(param_name="HVM-boot-params", key="order", value="dc"),
                dict(param_name="platform", key="device-model", value="qemu-upstream-uefi"),
            ),
            vdis=[dict(name="vm1 system disk", size="100GiB", device="xvda", userdevice="0")],
            cd_vbd=dict(device="xvdd", userdevice="3"),
            vifs=[dict(index=0, network_name=NETWORKS["MGMT"])],
        ))
    @pytest.mark.answerfile(
        lambda: AnswerFile("INSTALL").top_append(
            {"TAG": "source", "type": "local"},
            {"TAG": "primary-disk", "CONTENTS": "nvme0n1"},
        ))
    def test_install(self, vm_booted_with_installer):
        host_vm = vm_booted_with_installer
        installer.monitor_install(ip=host_vm.ip)

    def _test_firstboot(self, create_vms):
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
                     timeout_secs=10 * 60)
            ips = pxe.arp_addresses_for(mac_address)
            logging.info("Host VM has IPs %s", ips)
            assert len(ips) == 1
            host_vm.ip = ips[0]

            wait_for(
                lambda: commands.local_cmd(
                    ["nc", "-zw5", host_vm.ip, "22"], check=False).returncode == 0,
                "Wait for ssh back up on Host VM", retry_delay_secs=5, timeout_secs=4 * 60)

            # pool master must be reachable here
            pool = Pool(host_vm.ip)

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
                    logging.warning("investigating lack of %s service stamp", service)
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
    @pytest.mark.continuation_of([dict(vm="vm1",
                                       image_test="TestNested::test_install")])
    def test_boot_inst(self, create_vms):
        self._test_firstboot(create_vms)
