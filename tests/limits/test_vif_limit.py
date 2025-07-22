from pkgfixtures import host_with_saved_yum_state
import ipaddress
import logging
import os
import pytest
import tempfile

# Requirements:
# - one XCP-ng host (--host) >= 8.2
# - a VM (--vm)
# - the first network on the host can be used to reach the host

vif_limit = 16
interface_name = "enX"
vcpus = '8'

# There is a ResourceWarning due to background=True on an ssh call
# We do ensure the processes are killed
@pytest.mark.filterwarnings("ignore::ResourceWarning")
@pytest.mark.debian_uefi_vm
class TestVIFLimit:
    def test_vif_limit(self, host_with_saved_yum_state, imported_vm):
        host = host_with_saved_yum_state
        vm = imported_vm
        if (vm.is_running()):
            logging.info("VM already running, shutting it down first")
            vm.shutdown(verify=True)

        network_uuid = vm.vifs()[0].param_get('network-uuid')
        existing_vifs = len(vm.vifs())

        logging.info(f'Get {vcpus} vCPUs for the VM')
        vm.param_set('VCPUs-max', vcpus)
        vm.param_set('VCPUs-at-startup', vcpus)

        logging.info('Create VIFs before starting the VM')
        for i in range(existing_vifs, vif_limit):
            vm.create_vif(i, network_uuid=network_uuid)

        vm.start()
        vm.wait_for_os_booted()

        logging.info('Verify the interfaces exist in the guest')
        for i in range(0, vif_limit):
            if vm.ssh_with_result([f'test -d /sys/class/net/{interface_name}{i}']).returncode != 0:
                guest_error = vm.ssh_with_result(['dmesg | grep -B1 -A3 xen_netfront']).stdout
                logging.error("dmesg:\n%s", guest_error)
                assert False, "The interface does not exist in the guest, check dmesg output above for errors"

        logging.info('Configure interfaces')
        config = '\n'.join([f'iface {interface_name}{i} inet dhcp\n'
                            f'auto {interface_name}{i}'
                            for i in range(existing_vifs, vif_limit)])
        vm.ssh([f'echo "{config}" >> /etc/network/interfaces'])

        logging.info('Install iperf3 on VM and host')
        if vm.ssh_with_result(['apt install iperf3 --assume-yes']).returncode != 0:
            assert False, "Failed to install iperf3 on the VM"
        host.yum_install(['iperf3'])

        logging.info('Reconfigure VM networking')
        if vm.ssh_with_result(['systemctl restart networking']).returncode != 0:
            assert False, "Failed to configure networking"

        # Test iperf on all interfaces in parallel
        # Clean up on exceptions
        try:
            logging.info('Create separate iperf servers on the host')
            with tempfile.NamedTemporaryFile('w') as host_script:
                iperf_configs = [f'iperf3 -s -p {5100+i} &'
                                 for i in range(0, vif_limit)]
                host_script.write('\n'.join(iperf_configs))
                host_script.flush()
                host.scp(host_script.name, host_script.name)
                host.ssh([f'nohup bash -c "bash {host_script.name}" < /dev/null &>/dev/null &'],
                         background=True)

            logging.info('Start multiple iperfs on separate interfaces on the VM')
            with tempfile.NamedTemporaryFile('w') as vm_script:
                iperf_configs = [f'iperf3 --no-delay -c {host.hostname_or_ip} '
                                 f'-p {5100+i} --bind-dev {interface_name}{i} '
                                 f'--interval 0 --parallel 1 --time 30 &'
                                 for i in range(0, vif_limit)]
                vm_script.write('\n'.join(iperf_configs))
                vm_script.flush()
                vm.scp(vm_script.name, vm_script.name)
                stdout = vm.ssh([f'bash {vm_script.name}'])

            # TODO: log this into some performance time series DB
            logging.info(stdout)
        finally:
            vm.ssh(['pkill iperf3 || true'])
            host.ssh('killall iperf3')
