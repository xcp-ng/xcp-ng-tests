import logging

from lib import commands, pxe
from lib.commands import local_cmd, ssh
from lib.common import wait_for

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
        wait_for(lambda: local_cmd(f"ping -c1 {host_vm.ip} > /dev/null 2>&1", check=False),
                 "Wait for host up", timeout_secs=10 * 60, retry_delay_secs=10)
        wait_for(lambda: local_cmd(f"nc -zw5 {host_vm.ip} 22", check=False),
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
