import pytest

import logging
import re
import time

import paramiko
import pyte

from data import HOST_DEFAULT_PASSWORD, HOST_FREE_NICS
from lib.common import Defer
from lib.host import Host

# Test requirements:
# - An XCP-ng host with 2 network interfaces:
#   * one for management
#   * another one to test the renaming

@pytest.mark.parametrize("reset_nics", [False, True], ids=["no_reset", "reset_nics"])
def test_retain_mgmt_iface(
    host: Host,
    reset_nics: bool,
    defer: Defer,
):

    # Besides the management interface, the target host needs to have another
    # interface that will be renamed during this test
    if not HOST_FREE_NICS:
        pytest.fail("There are no available free NICs for testing.")

    target_iface = HOST_FREE_NICS[0]

    iface_mac = host.ssh(f"interface-rename --list | awk '/{target_iface}/ {{ print $2 }}'")

    all_ifaces = host.ssh("ip -o link show | awk -F': ' '{print $2}'").split()

    # Find a non-existing interface name
    i = 0
    while f"eth{i}" in all_ifaces:
        i += 1
    new_iface_name = f"eth{i}"

    host.ssh(f"ip link set {target_iface} down")

    target_pif_uuid = host.xe("pif-list", {"device": target_iface, "host-uuid": host.uuid, "minimal": True})
    if target_pif_uuid:
        host.xe("pif-forget", {"uuid": target_pif_uuid})

    host.ssh(f"ip link set {target_iface} name {new_iface_name}")
    host.ssh(f"ip link set {new_iface_name} up")

    def cleanup():
        if new_iface_name in host.ssh("ip link show"):
            host.ssh(f"ip link set {new_iface_name} down")
            host.ssh(f"ip link set {new_iface_name} name {target_iface}")
            host.ssh(f"ip link set {target_iface} up")
        new_pif_uuid = host.xe("pif-list", {"device": new_iface_name, "host-uuid": host.uuid, "minimal": True})
        if new_pif_uuid:
            host.xe("pif-forget", {"uuid": new_pif_uuid})

    defer(cleanup)

    host.ssh(f"interface-rename --update {new_iface_name}='{iface_mac}'")
    host.xe("pif-scan", {"host-uuid": host.uuid})

    if reset_nics:
        logging.info("Performing an emergency network reset, including interface names")
    else:
        logging.info("Performing an emergency network reset without resetting interface names")
    emergency_network_reset(host, reset_nics, defer)

    # wait for host to be powered-up again and XAPI services to start
    host.wait_for_host_down()
    host.wait_for_host_up()
    host.wait_for_ssh_reachable()
    host.wait_for_xapi_enabled()

    # reboot_iface = host.ssh(f"xe pif-list MAC={iface_mac} params=device host-uuid={host.uuid} --minimal")
    reboot_iface = host.xe("pif-list", {"MAC": iface_mac, "params": "device", "host-uuid": host.uuid, "minimal": True})

    if reset_nics:
        assert reboot_iface != new_iface_name, (
            "The management interface has NOT changed from {new_iface_name} to {target_iface}"
        )
    else:
        assert reboot_iface == new_iface_name, (
            f"The management interface has changed from {new_iface_name} to '{reboot_iface}'"
        )


def emergency_network_reset(host: Host, reset_nics: bool, defer: Defer) -> None:
    logging.getLogger("paramiko").setLevel(logging.WARNING)

    class IgnorePolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            pass

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(IgnorePolicy())
    defer(client.close)
    client.connect(host.hostname_or_ip, username="root")
    transport = client.get_transport()
    assert transport is not None
    channel = transport.open_session()
    channel.get_pty(term='vt100', width=80, height=24)
    command = "xsconsole"
    channel.exec_command(command.encode())
    channel.settimeout(30.0)
    screen = pyte.Screen(columns=80, lines=24)
    screen.define_charset("U", "(")
    stream = pyte.ByteStream(screen)
    stream.select_other_charset("@")

    ENTER = "\r"
    DOWN = "\x1bOB"
    BUFFER_READ_SIZE = 4096
    STABILIZE_TIME = 0.5  # seconds

    def refresh_screen():
        while channel.recv_ready():
            stream.feed(channel.recv(BUFFER_READ_SIZE))

    def wait_for_screen_to_stabilize() -> None:
        time.sleep(STABILIZE_TIME)
        while channel.recv_ready():
            refresh_screen()
            time.sleep(STABILIZE_TIME)

    def has_content_on_screen(pattern: str) -> bool:
        return any(pattern in line for line in screen.display)

    def wait_for_screen_content(pattern: str) -> None:
        while not has_content_on_screen(pattern):
            stream.feed(channel.recv(BUFFER_READ_SIZE))

    def get_highlighted() -> list[str]:
        highlighted = []
        for i in range(screen.lines):
            row = screen.buffer[i]
            previously_reversed = False
            for j in range(screen.columns):
                char = row[j]
                if char.reverse and not previously_reversed:
                    highlighted.append("")
                if char.reverse:
                    highlighted[-1] += char.data
                previously_reversed = char.reverse
        return highlighted

    def send_keys(keys: str) -> None:
        channel.send(keys.encode())

    def extract_value(key: str) -> str:
        for i in range(screen.lines):
            row = screen.buffer[i]
            content = screen.display[i]
            for match in re.finditer(key, content):
                if all(row[j].bold for j in range(match.start(), match.end())):
                    value, *_ = content[match.end():].split()
                    return value
        raise ValueError(f"Key {key!r} not found")

    def show_screen() -> str:
        ANSI_RESET = "\033[0m"
        ANSI_BOLD = "\033[1m"
        ANSI_REVERSE = "\033[7m"

        columns = screen.columns

        # 1. Draw the top border
        result = ["┌" + "─" * columns + "┐"]

        # 2. Draw each row with left and right borders
        for row_idx in range(screen.lines):
            row = screen.buffer[row_idx]

            # Start with the left border wall
            row_str = "│"

            for col_idx in range(columns):
                char = row[col_idx]

                fmt = ""
                if char.bold:
                    fmt += ANSI_BOLD
                if char.reverse:
                    fmt += ANSI_REVERSE

                if fmt:
                    row_str += f"{fmt}{char.data}{ANSI_RESET}"
                else:
                    row_str += char.data

            # Cap the line with the right border wall
            row_str += "│"

            result.append(row_str)

        # 3. Draw the bottom border
        result.append("└" + "─" * columns + "┘")
        return "\n".join(result)

    def debug_screen(title: str = "xsconsole screen") -> None:
        logging.debug(f"{title}:\n{show_screen()}")

    # Wait for the welcome screen to appear
    wait_for_screen_content("XCP-ng")
    wait_for_screen_to_stabilize()
    debug_screen("Welcome screen")
    assert has_content_on_screen("─ Configuration ─")
    assert get_highlighted() == ["Status Display"]

    # Send "n" like "network"
    send_keys("n")
    wait_for_screen_to_stabilize()
    debug_screen("Network menu item highlighted")
    assert get_highlighted() == ["Network and Management Interface"]

    # Extract network configuration
    dhcp = extract_value("DHCP/Static IP")
    ip_address = extract_value("IP address")
    netmask = extract_value("Netmask")
    gateway = extract_value("Gateway")
    assert dhcp in ("Static", "DHCP")

    # Press Enter to select the Network and Management Interface menu
    send_keys(ENTER)
    wait_for_screen_to_stabilize()
    debug_screen("Network screen")
    assert has_content_on_screen("─ Configuration ─")
    assert get_highlighted() == ["Configure Management Interface"]

    # Send "e" like "emergency"
    send_keys("e")
    wait_for_screen_to_stabilize()
    debug_screen("Emergency Network Reset menu item highlighted")
    assert get_highlighted() == ["Emergency Network Reset"]

    # Press Enter to select Emergency Network Reset
    send_keys(ENTER)
    wait_for_screen_to_stabilize()
    debug_screen("Login screen")
    assert has_content_on_screen("─ Login ─")

    # Type password
    send_keys(f"\t{HOST_DEFAULT_PASSWORD}")
    wait_for_screen_to_stabilize()
    debug_screen("Password typed")

    # Press Enter to login
    send_keys(ENTER)
    wait_for_screen_to_stabilize()
    debug_screen("Emergency network reset warning")
    assert has_content_on_screen("─ Emergency Network Reset ─")

    # Press Enter to acknowledge the warning
    send_keys(ENTER)
    wait_for_screen_to_stabilize()
    debug_screen("Choosing primary network interface")
    assert has_content_on_screen("─ Emergency Network Reset ─")
    _, highlighted = get_highlighted()
    assert highlighted == "eth0"

    # Press Enter to accept eth0 as primary interface, then Enter again to proceed
    send_keys(ENTER * 2)
    wait_for_screen_to_stabilize()
    debug_screen("Choose whether to reset the interface names to default or not")
    assert has_content_on_screen("─ Emergency Network Reset ─")
    _, highlighted = get_highlighted()
    assert highlighted in ("Rename", "Yes")

    if not reset_nics:
        send_keys(DOWN)
        wait_for_screen_to_stabilize()
        debug_screen("Do not reset the network interface names")
        assert has_content_on_screen("─ Emergency Network Reset ─")
        _, highlighted = get_highlighted()
        assert highlighted in ("Keep name", "No")

    # Press Enter to confirm the interface naming choice
    send_keys(ENTER)
    wait_for_screen_to_stabilize()
    debug_screen("Select IP configuration mode to use after reset")
    assert has_content_on_screen("─ Emergency Network Reset ─")
    _, highlighted = get_highlighted()
    assert highlighted == "DHCP"

    # Press "s" to select "Static"
    if dhcp == "Static":
        send_keys("s")
        wait_for_screen_to_stabilize()
        debug_screen("Select Static mode")
        assert has_content_on_screen("─ Emergency Network Reset ─")
        _, highlighted = get_highlighted()
        assert highlighted == "Static"

    # Validate either "DHCP" or "Static"
    send_keys(ENTER)
    wait_for_screen_to_stabilize()

    # Configure a static IP
    if dhcp == "Static":
        debug_screen("Static IP configuration dialog")
        assert has_content_on_screen("─ Emergency Network Reset ─")
        send_keys(ip_address)
        send_keys(ENTER)
        send_keys(netmask)
        send_keys(ENTER)
        send_keys(gateway)
        send_keys(ENTER)
        send_keys(gateway)  # Use gateway as DNS
        wait_for_screen_to_stabilize()
        debug_screen("Static IP configured")
        send_keys(ENTER)
        wait_for_screen_to_stabilize()

    # Confirmation dialog
    debug_screen("Confirmation dialog")
    assert has_content_on_screen("Press <Enter> to reset the network configuration.")

    send_keys(ENTER)
    wait_for_screen_to_stabilize()
    debug_screen("Reboot screen")
