import logging
import time

import pytest

from lib.host import Host

# Requirements:
# - an XCP-ng host (--hosts) with a PCIe slot that supports hotplug and has a device
#   that is safe to remove (not a NIC carrying the management network, not boot storage).
#   Typically a GPU or secondary NIC works well.
# - The host's PCIe topology must expose at least one hotplug-capable slot via
#   /sys/bus/pci/slots/<N>/power.
#
# The test exercises pciehp_disable_slot (and pciehp_enable_slot) in the dom0 kernel
# by writing to the slot's power sysfs attribute.


def _devices_in_slot(host: Host, slot: str) -> list[str]:
    """Return PCI addresses of devices that belong to the given hotplug slot.

    /sys/bus/pci/slots/<slot>/address contains the address as domain:bus:device
    with no function suffix (e.g. "0000:00:03").  All functions of the device
    in that slot appear in the flat /sys/bus/pci/devices/ view as entries
    matching that prefix (e.g. "0000:00:03.0", "0000:00:03.1", ...).
    """
    addr_prefix = host.ssh(f"cat /sys/bus/pci/slots/{slot}/address 2>/dev/null || true").strip()
    if not addr_prefix:
        return []
    # Normalise: the file may omit the domain, e.g. "00:03" instead of "0000:00:03"
    if addr_prefix.count(":") == 1:
        addr_prefix = f"0000:{addr_prefix}"
    addrs = host.ssh(
        f"ls /sys/bus/pci/devices/ 2>/dev/null | grep -F '{addr_prefix}.' || true"
    ).split()
    logging.warning(f"{addrs=}")
    return addrs


def _find_safe_hotplug_slot(host: Host) -> tuple[str, str]:
    """Return (slot_name, pci_address) for a hotplug-capable slot we can safely cycle.

    We skip slots whose device matches any driver bound to a NIC that carries the
    management IP or any disk/storage controller.
    """
    slots_raw = host.ssh("ls /sys/bus/pci/slots/ 2>/dev/null || true")
    if not slots_raw.strip():
        pytest.skip("No PCIe hotplug slots found in /sys/bus/pci/slots/")

    mgmt_pci = _management_pci_addresses(host)

    for slot in slots_raw.split():
        # A slot without a 'power' file cannot be controlled via pciehp
        if host.ssh_with_result(f"test -f /sys/bus/pci/slots/{slot}/power").returncode != 0:
            continue

        addrs = _devices_in_slot(host, slot)
        if not addrs:
            continue

        safe = True
        for addr in addrs:
            if addr in mgmt_pci:
                logging.info("Skipping slot %s (%s): management network device", slot, addr)
                safe = False
                break
            # Skip storage controllers (class 0x01xx)
            dev_class = host.ssh(
                f"cat /sys/bus/pci/devices/{addr}/class 2>/dev/null || echo 0x000000"
            ).strip()
            if dev_class.startswith("0x01"):
                logging.info("Skipping slot %s (%s): storage controller", slot, addr)
                safe = False
                break

        if safe:
            return slot, addrs[0]

    pytest.skip("No safe hotplug-capable PCIe slot found (all occupied by critical devices)")


def _management_pci_addresses(host: Host) -> set[str]:
    """Return the set of PCI addresses backing the management network interface."""
    mgmt_if = host.ssh(
        "ip -o link show | awk -F': ' '{print $2}' | while read iface; do "
        "  ip addr show $iface | grep -q ' $(xe pif-list params=IP --minimal) ' 2>/dev/null && echo $iface; "
        "done || true"
    ).strip()

    addrs: set[str] = set()
    if not mgmt_if:
        return addrs

    # Walk sysfs to find the PCI device behind the interface
    pci_path = host.ssh(
        f"readlink -f /sys/class/net/{mgmt_if}/device 2>/dev/null || true"
    ).strip()
    if pci_path:
        # Extract the PCI address component (e.g. 0000:03:00.0)
        parts = pci_path.split("/")
        for part in parts:
            if len(part) >= 12 and ":" in part:
                addrs.add(part)
    return addrs


class TestPCIeHotplug:
    """Exercise the pciehp_disable_slot / pciehp_enable_slot kernel paths in dom0."""

    def test_pciehp_disable_enable_slot(self, host: Host) -> None:
        slot, pci_addr = _find_safe_hotplug_slot(host)
        logging.info("Using PCIe slot %s (device %s) for hotplug test", slot, pci_addr)

        # Stamp dmesg so we can grep only the messages produced by this test
        marker = f"pciehp-test-{slot}"
        host.ssh(f"echo '{marker}' > /dev/kmsg")

        # --- Disable the slot (triggers pciehp_disable_slot) ---
        logging.info("Disabling slot %s via sysfs", slot)
        host.ssh(f"echo 0 > /sys/bus/pci/slots/{slot}/power")

        # Give pciehp a moment to complete the hot-remove sequence
        time.sleep(2)

        # Device should no longer appear in sysfs
        result = host.ssh_with_result(f"test -e /sys/bus/pci/devices/{pci_addr}")
        assert result.returncode != 0, (
            f"Device {pci_addr} is still present after slot {slot} was disabled"
        )

        # Kernel should have logged a removal event
        dmesg_after_remove = host.ssh(f"dmesg | grep -A 9999 '{marker}'")
        assert "pciehp" in dmesg_after_remove or "pcie_hp" in dmesg_after_remove, (
            "Expected pciehp messages in dmesg after slot disable"
        )

        # --- Re-enable the slot (triggers pciehp_enable_slot) ---
        logging.info("Re-enabling slot %s via sysfs", slot)
        host.ssh(f"echo 1 > /sys/bus/pci/slots/{slot}/power")

        # Wait for enumeration to complete
        time.sleep(5)

        result = host.ssh_with_result(f"test -e /sys/bus/pci/devices/{pci_addr}")
        assert result.returncode == 0, (
            f"Device {pci_addr} did not re-appear after slot {slot} was re-enabled"
        )

        dmesg_after_enable = host.ssh(f"dmesg | grep -A 9999 '{marker}'")
        assert "pciehp" in dmesg_after_enable or "pcie_hp" in dmesg_after_enable, (
            "Expected pciehp messages in dmesg after slot enable"
        )
