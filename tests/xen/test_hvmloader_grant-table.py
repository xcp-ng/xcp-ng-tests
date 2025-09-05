import pytest

import logging
import re

# hvmloader PCI bar caching tests
#
# Requirements:
# - XCP-ng host_at_least_8_3
# - A Linux VM

@pytest.mark.usefixtures("host_at_least_8_3")
@pytest.mark.small_vm
@pytest.mark.usefixtures("running_unix_vm")
class TestXenPlatformPciBarUc:
    @pytest.fixture
    def change_xen_platform_pci_bar_uc_to_false(self, host):
        host.ssh(['echo', 'xen-platform-pci-bar-uc=false', '>', '/etc/xenopsd.conf.d/hvmloader.conf'])
        host.restart_toolstack(verify=True)
        yield host
        host.ssh(['rm', '/etc/xenopsd.conf.d/hvmloader.conf'])
        host.restart_toolstack(verify=True)

    @pytest.fixture
    def restarted_unix_vm(self, change_xen_platform_pci_bar_uc_to_false, running_unix_vm):
        running_unix_vm.shutdown(verify=True)
        running_unix_vm.start(on=change_xen_platform_pci_bar_uc_to_false.uuid)
        running_unix_vm.wait_for_vm_running_and_ssh_up()
        return running_unix_vm

    @pytest.fixture
    def xen_platform_pci_id(self, running_unix_vm):
        lspci_output = running_unix_vm.ssh(['lspci', '-k'])
        regexp = r'^(.*) Class 0100: 5853:0001 xen-platform-pci$'

        m = re.search(regexp, lspci_output, re.MULTILINE)
        if m is None:
            pytest.fail("'xen-platform-pci' PCI ID not found")
        return m.group(1)

    @pytest.fixture
    def xen_platform_pci_bar_uc(self, host):
        pci_bar_uc_value = host.ssh(['grep', '-r', '"xen-platform-pci-bar-uc"', '/etc/xenopsd.conf.d/'], check=False)
        regexp = r'.*:\s*xen-platform-pci-bar-uc\s*=\s*((true|false))'

        m = re.match(regexp, pci_bar_uc_value)
        if m is not None and m.group(1) == "true":
            pytest.fail("'xen-platform-pci-bar-uc' found in '/etc/xenopsd.conf.d/' with value 'true'.")
        # no teardown

    @pytest.fixture
    def xen_platform_pci_io_address(self, running_unix_vm, xen_platform_pci_id):
        # Find 'xen-platform-pci' IO mem resource address.
        pci_resource_output = running_unix_vm.ssh(
            ['cat', '/sys/devices/pci0000:00/0000:{}/resource'.format(xen_platform_pci_id)])

        # First line is taken by PCI IO port, select 2nd line for IO mem.
        address_line = pci_resource_output.splitlines()[1].split()
        start_address = int(address_line[0], 16)
        return start_address

    @pytest.fixture
    def mtrr_ranges_list(self, running_unix_vm):
        # List and check MTRR ranges.
        mtrr_output = running_unix_vm.ssh(['cat', '/proc/mtrr'])
        return mtrr_output.splitlines()

    # Check the default behavior when 'xen_platform_pci_bar_uc' is true
    def test_xen_platform_pci_bar_uc_true(self, xen_platform_pci_io_address, mtrr_ranges_list):

        logging.info(f"'xen-platform-pci' PCI IO mem address is 0x{xen_platform_pci_io_address:08X}")

        regexp = r'reg(.*): base=(0x[0-9a-f]*) \(.*\), size=(.*)MB, count=(.*): (.*)'
        outside = True

        for mtrr_range in mtrr_ranges_list:
            m = re.match(regexp, mtrr_range)
            base_address = int(m.group(2), 16)
            size = int(m.group(3)) * 1024 * 1024
            cache_mode = m.group(5)

            # Compare 'xen-platform-pci-bar-uc' setting and MTRR configuration.
            if xen_platform_pci_io_address >= base_address and \
                    xen_platform_pci_io_address < base_address + size and cache_mode == "uncachable":
                outside = False
                logging.info("Grant-tables are inside the 'uncachable' mapping as expected.")
                break
            else:
                logging.info("Grant-tables are outside of this MTRR range. Check other ranges...")

        if outside is True:
            pytest.fail("Grant_tables are outside of the MTRR ranges but 'xen-platform-pci-bar-uc' is true")

    # Check the alternate behavior when 'xen_platform_pci_bar_uc' is explicitely set to false
    def test_xen_platform_pci_bar_uc_false(self, change_xen_platform_pci_bar_uc_to_false,
                                           restarted_unix_vm, xen_platform_pci_io_address,
                                           mtrr_ranges_list):

        logging.info(f"'xen-platform-pci' PCI IO mem address is 0x{xen_platform_pci_io_address:08X}")

        regexp = r'reg(.*): base=(0x[0-9a-f]*) \(.*\), size=(.*)MB, count=(.*): (.*)'

        for mtrr_range in mtrr_ranges_list:
            m = re.match(regexp, mtrr_range)
            base_address = int(m.group(2), 16)
            size = int(m.group(3)) * 1024 * 1024
            cache_mode = m.group(5)

            # Compare 'xen-platform-pci-bar-uc' setting and MTRR configuration.
            if xen_platform_pci_io_address >= base_address and \
                    xen_platform_pci_io_address < base_address + size and cache_mode == "uncachable":
                pytest.fail("Grant-tables are inside of the MTRR ranges but 'xen-platform-pci-bar-uc' is false.")

        logging.info("Grant-tables are outside of the MTRR ranges as expected.")
