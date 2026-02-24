#!/usr/bin/env python3

import argparse
import logging
import os
import sys
import threading

sys.path.append(f"{os.path.abspath(os.path.dirname(__file__))}/..") # noqa
from lib.commands import ssh, SSHCommandFailed
from lib.common import is_uuid
from lib.pool import Pool

logging.basicConfig()
logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format='[%(levelname)s] %(message)s', level=logging.INFO)

def is_ip_active(ip):
    return not os.system(f"ping -c 3 -W 10 {ip} > /dev/null 2>&1")

def is_ssh_up(ip):
    try:
        ssh(ip, ['true'], options=['-o "ConnectTimeout 10"'])
        return True
    except SSHCommandFailed:
        # probably not up yet
        return False

def is_new_host_ready(ip_address):
    try:
        output = ssh(ip_address, ['xe', 'host-list', 'enabled=true', '--minimal'])
        return is_uuid(output)
    except Exception:
        return False

def  install_updates_reboot(host):
    try:
        logging.info('Running updates on the host:' + str(host))
        # host.install_updates()
        logging.info('Reboot the host:' + str(host))
        host.reboot(verify=True)
        # host.reboot()
        # wait_for(lambda: not os.system(f"nc -zw5 {host} 22"),
        #              "Wait for ssh up on host", timeout_secs=10 * 60, retry_delay_secs=5)
        return(True)
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "host",
        help="hostname or IP address of the host which is the primary of the pool which will be updated"
    )
    args = parser.parse_args()

    # Verify the host's IP address
    logging.info('Testing IP: ' + args.host)
    # TODO : How to manage and validate IPv6?

    # verify that the host is available (ssh) and that it is indeed the master of a pool
    # TODO: Exit the script if there's a problem, or confirm that it's working correctly.

    # TODO: replace with an existing function in host or pool
    if not is_ssh_up(args.host):
        raise Exception(f"Could not SSH into host `{args.host}`")

    try:
        pool = Pool(args.host) # will fail if host is not XCP-ng or XAPI doesn't respond yet
    except Exception as e:
        raise Exception(f"Host `{args.host}` isn't ready or isn't an XCP-ng host")

    host = pool.master
    logging.info('IP of the master is: %s', args.host)
    assert host.is_enabled()
    # TODO: que faire si pas ok ?

    # We build a list of secondaries, if they exist because we will run updates and reboots in parallel on these,
    # once the primary one is done and available.
    slaves_list = pool.hosts[1:] 
    logging.info('Here is the slaves list: %s', slaves_list)

    # TODO: Adding a feature/setting to add/enable repos if requested?

    # We check if there are any updates to run. If not, we exit saying so.
    # TODO Add yum clean metadata - create a function ?
    update_to_do = host.has_updates()
    if not update_to_do:
        logging.info('No updates to do.')
        return
    print(update_to_do)
    logging.info('DO I NEED TO MAKE UPDATES:  ' + str(update_to_do))

    # There are updates to be done, we do them on the main one, then we reboot it.
    mupdates = install_updates_reboot(host)
    if not mupdates:
        logging.info('Errors durring the update process on the master: ' + str(host))
        return        

    logging.info('How many secondaries? ' + str(len(slaves_list)))

    # Management of secondary servers, if any.
    if len(slaves_list) == 0 :
        logging.info('No other servers to process in the pool.')
        return
    
    if len(slaves_list) == 1 :
        print(slaves_list[0])
        logging.info('ONE SERVER TO DO')
        # logging.info(slaves_list[0].install_updates())
        # slaves_list[0].reboot()
        install_updates_reboot(host)
    elif len(slaves_list) > 1 :
        logging.info('MULTIPLE SERVERS TO DO')
        # Several secondary processes. We're using multithreading for updates and reboots, all at the same time.
        threads = []
        for secondary in slaves_list:
            # Add function in the thread below where the desired secondary is passed.
            # t = threading.Thread(target=print(secondary))
            t = threading.Thread(target=install_updates_reboot, args=(secondary,))
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join()


    # TODO: when everyone is up to date, say it

    # TODO: 
    # For the VMs, we want to take snapshots at the end.
    # How do we know where the VM is? Pass the host as a parameter? So add two parameters, one of which is optional for the VMs?
    # We need to know if the VM and if the VM will take a snapshot. But we need to know the VM's UUID and the host's IP for it to take the snapshot.
    # Local file for testing, read the netbox (to be updated?), read the GRIST table?

if __name__ == '__main__':
    main()