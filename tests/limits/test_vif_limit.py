import pytest

import ipaddress
import logging
import os
import tempfile

from lib.host import Host
from lib.vm import VM
from pkgfixtures import host_with_saved_yum_state

# Requirements:
# - one XCP-ng host (--host) >= 8.2
# - a Debian VM (--vm)
# - the first network on the host can be used to reach the host

VIF_LIMIT = 16
VCPUS = '8'

# There is a ResourceWarning due to background=True on an ssh call
# We do ensure the processes are killed
@pytest.mark.filterwarnings("ignore::ResourceWarning")
@pytest.mark.debian_uefi_vm
class TestVIFLimit:
    def test_vif_limit(self, host_with_saved_yum_state: Host, imported_vm: VM) -> None:
        host = host_with_saved_yum_state
        vm = imported_vm
        interface_name = "enX"

        if (vm.is_running()):
            logging.info("VM already running, shutting it down first")
            vm.shutdown(verify=True)

        network_uuid = vm.vifs()[0].param_get('network-uuid')
        existing_vifs = len(vm.vifs())

        logging.info(f'Get {VCPUS} vCPUs for the VM')
        original_vcpus_max = vm.param_get('VCPUs-max')
        original_vcpus_at_startup = vm.param_get('VCPUs-at-startup')
        vm.param_set('VCPUs-max', VCPUS)
        vm.param_set('VCPUs-at-startup', VCPUS)

        logging.info('Create VIFs before starting the VM')
        vifs = []
        for i in range(existing_vifs, VIF_LIMIT):
            vif = vm.create_vif(i, network_uuid=network_uuid)
            vifs.append(vif)

        vm.start()
        vm.wait_for_os_booted()
        try:
            logging.info('Verify the interfaces exist in the guest')
            for i in range(0, VIF_LIMIT):
                if vm.ssh_with_result(f'test -d /sys/class/net/{interface_name}{i}').returncode != 0:
                    guest_error = vm.ssh_with_result('dmesg | grep -B1 -A3 xen_netfront').stdout
                    logging.error("dmesg:\n%s", guest_error)
                    assert False, "The interface does not exist in the guest, check dmesg output above for errors"

            logging.info('Configure interfaces')
            config = '\n'.join([f'iface {interface_name}{i} inet dhcp\n'
                                f'auto {interface_name}{i}'
                                for i in range(existing_vifs, VIF_LIMIT)])
            vm.ssh(f'echo "{config}" >> /etc/network/interfaces')

            logging.info('Install iperf3 on VM and host')
            if vm.ssh_with_result('apt install iperf3 --assume-yes').returncode != 0:
                assert False, "Failed to install iperf3 on the VM"
            host.yum_install(['iperf3'])

            logging.info('Reconfigure VM networking')
            if vm.ssh_with_result('systemctl restart networking').returncode != 0:
                assert False, "Failed to configure networking"

            # Test iperf on all interfaces in parallel
            # Clean up on exceptions
            logging.info('Create separate iperf servers on the host')
            with tempfile.NamedTemporaryFile('w') as host_script:
                iperf_configs = [f'iperf3 -s -p {5100+i} &'
                                 for i in range(0, VIF_LIMIT)]
                host_script.write('\n'.join(iperf_configs))
                host_script.flush()
                host.scp(host_script.name, host_script.name)
                host.ssh(f'nohup bash -c "bash {host_script.name}" < /dev/null &>/dev/null &',
                         background=True)

            logging.info('Start multiple iperfs on separate interfaces on the VM')
            with tempfile.NamedTemporaryFile('w') as vm_script:
                iperf_configs = [f'iperf3 --no-delay -c {host.hostname_or_ip} '
                                 f'-p {5100+i} --bind-dev {interface_name}{i} '
                                 f'--interval 0 --parallel 1 --time 30 &'
                                 for i in range(0, VIF_LIMIT)]
                vm_script.write('\n'.join(iperf_configs))
                vm_script.flush()
                vm.scp(vm_script.name, vm_script.name)
                stdout = vm.ssh(f'bash {vm_script.name}')

            # TODO: log this into some performance time series DB
            logging.info(stdout)
        finally:
            vm.ssh('pkill iperf3 || true')
            vm.shutdown(verify=True)
            vm.param_set('VCPUs-at-startup', original_vcpus_at_startup)
            vm.param_set('VCPUs-max', original_vcpus_max)
            for vif in vifs:
                vif.destroy()
            host.ssh('killall iperf3 || true')
