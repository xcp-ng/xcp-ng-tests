import pytest

import logging
import os
import re

# AMD grant-tables cachability parameter tests.
# This file contains 2 tests to check if the hvmloader PCI bar fix is applied or not
# by xenopsd. Tests check the xenopsd configuration and then compare with what is
# seen inside the VM. If the grant-tables are cached, it means the fix is applied.
#
# Requirements:
# - At least one XCP-ng host (>=8.3)
# - A Linux VM

def xen_platform_pci_id(vm):
    pci_path = vm.ssh(['ls', '-d', '/sys/bus/pci/drivers/xen-platform-pci/0000:*'])
    if pci_path is not None:
        return os.path.basename(pci_path)
    pytest.fail("'xen-platform-pci' PCI not found")

def xen_platform_pci_io_address(vm, xen_platform_pci_id):
    # Find 'xen-platform-pci' IO mem resource address.
    pci_resource_output = vm.ssh(
        ['cat', '/sys/devices/pci0000:00/{}/resource'.format(xen_platform_pci_id)])

    # First line is taken by PCI IO port, select 2nd line for IO mem.
    address_line = pci_resource_output.splitlines()[1].split()
    start_address = int(address_line[0], 16)
    return start_address

def mtrr_ranges(vm):
    # List and check MTRR ranges.
    return vm.ssh(['cat', '/proc/mtrr']).splitlines()

def are_grant_tables_inside_uncachable_mapping(mtrr_ranges_list, pci_io_address):
    regexp = r'reg(.*): base=(0x[0-9a-f]*) \(.*\), size=(.*)MB, count=(.*): (.*)'

    for mtrr_range in mtrr_ranges_list:
        m = re.match(regexp, mtrr_range)
        assert m is not None
        base_address = int(m.group(2), 16)
        size = int(m.group(3)) * 1024 * 1024
        cache_mode = m.group(5)

        # Check that PCI io memory address is inside MTRR configuration.
        if pci_io_address >= base_address and \
                pci_io_address < base_address + size and cache_mode == "uncachable":
            return True
    return False

@pytest.mark.usefixtures("host_at_least_8_3")
@pytest.mark.small_vm
@pytest.mark.usefixtures("running_unix_vm")
class TestXenPlatformPciBarUc:
    @pytest.fixture
    def host_with_xen_platform_pci_bar_uc_set_to_true(self, host):
        host.ssh(['echo', 'xen-platform-pci-bar-uc=true', '>', '/etc/xenopsd.conf.d/hvmloader.conf'])
        host.restart_toolstack(verify=True)
        yield host
        host.ssh(['rm', '/etc/xenopsd.conf.d/hvmloader.conf'])
        host.restart_toolstack(verify=True)

    @pytest.fixture
    def vm_with_xen_platform_pci_bar_uc_set_to_true(self, host_with_xen_platform_pci_bar_uc_set_to_true, unix_vm):
        if unix_vm.is_running():
            unix_vm.shutdown(verify=True)
        unix_vm.start(on=host_with_xen_platform_pci_bar_uc_set_to_true.uuid)
        unix_vm.wait_for_vm_running_and_ssh_up()
        return unix_vm

    @pytest.fixture
    def host_with_xen_platform_pci_bar_uc_set_to_false(self, host):
        host.ssh(['echo', 'xen-platform-pci-bar-uc=false', '>', '/etc/xenopsd.conf.d/hvmloader.conf'])
        host.restart_toolstack(verify=True)
        yield host
        host.ssh(['rm', '/etc/xenopsd.conf.d/hvmloader.conf'])
        host.restart_toolstack(verify=True)

    @pytest.fixture
    def vm_with_xen_platform_pci_bar_uc_set_to_false(self, host_with_xen_platform_pci_bar_uc_set_to_false, unix_vm):
        if unix_vm.is_running():
            unix_vm.shutdown(verify=True)
        unix_vm.start(on=host_with_xen_platform_pci_bar_uc_set_to_false.uuid)
        unix_vm.wait_for_vm_running_and_ssh_up()
        return unix_vm

    # Check the default behavior when 'xen_platform_pci_bar_uc' is true
    def test_xen_platform_pci_bar_uc_true(self, host_with_xen_platform_pci_bar_uc_set_to_true,
                                          vm_with_xen_platform_pci_bar_uc_set_to_true):
        pci_id = xen_platform_pci_id(vm_with_xen_platform_pci_bar_uc_set_to_true)
        pci_io_address = xen_platform_pci_io_address(vm_with_xen_platform_pci_bar_uc_set_to_true, pci_id)
        logging.info(f"'xen-platform-pci' PCI IO mem address is 0x{pci_io_address:016X}")
        mtrr_ranges_list = mtrr_ranges(vm_with_xen_platform_pci_bar_uc_set_to_true)

        if are_grant_tables_inside_uncachable_mapping(mtrr_ranges_list, pci_io_address):
            logging.info("Grant-tables are inside the 'uncachable' mapping as expected.")
        else:
            pytest.fail("Grant_tables are outside of the uncachable MTRR ranges but 'xen-platform-pci-bar-uc' is true")

    # Check the alternate behavior when 'xen_platform_pci_bar_uc' is explicitely set to false
    def test_xen_platform_pci_bar_uc_false(self, host_with_xen_platform_pci_bar_uc_set_to_false,
                                           vm_with_xen_platform_pci_bar_uc_set_to_false):
        pci_id = xen_platform_pci_id(vm_with_xen_platform_pci_bar_uc_set_to_false)
        pci_io_address = xen_platform_pci_io_address(vm_with_xen_platform_pci_bar_uc_set_to_false, pci_id)
        logging.info(f"'xen-platform-pci' PCI IO mem address is 0x{pci_io_address:016X}")
        mtrr_ranges_list = mtrr_ranges(vm_with_xen_platform_pci_bar_uc_set_to_false)

        if are_grant_tables_inside_uncachable_mapping(mtrr_ranges_list, pci_io_address):
            pytest.fail("Grant-tables are inside of the uncachable MTRR ranges but 'xen-platform-pci-bar-uc' is false.")
        else:
            logging.info("Grant-tables are outside of the uncachable MTRR ranges as expected.")
