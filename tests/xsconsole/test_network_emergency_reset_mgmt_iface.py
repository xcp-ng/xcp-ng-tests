import pytest

import logging
import time

import paramiko
import pyte

from lib.common import Defer
from lib.host import Host

# Test requirements:
# - An XCP-ng host with 2 network interfaces:
#   * eth0 for management, configured with DHCP
#   * eth1 to test the renaming, no configuration needed
# - Note that test relies on the DHCP providing the same IP address
#   after an emergency network reset


@pytest.mark.parametrize("reset_nics", [False, True], ids=["no_reset", "reset_nics"])
def test_retain_mgmt_iface(
    host: Host,
    reset_nics: bool,
    defer: Defer,
):
    from data import HOST_DEFAULT_PASSWORD

    # Besides the management interface (eth0), the target host needs to
    # have another interface (eth1) that will be renamed during this test
    eth1_mac = host.ssh("interface-rename --list | grep -i eth1 | awk '{ print $2 }'")

    host.ssh("ip link set eth1 down")
    eth1_pif_uuid = host.ssh("xe pif-list device=eth1 --minimal")
    if eth1_pif_uuid:
        host.ssh(f"xe pif-forget uuid={eth1_pif_uuid}")
    host.ssh("ip link set eth1 name eth999")
    host.ssh("ip link set eth999 up")

    def cleanup():
        if "eth999" in host.ssh("ip link show"):
            host.ssh("ip link set eth999 down")
            host.ssh("ip link set eth999 name eth1")
            host.ssh("ip link set eth1 up")
        eth999_pif_uuid = host.ssh("xe pif-list device=eth999 --minimal")
        if eth999_pif_uuid:
            host.ssh(f"xe pif-forget uuid={eth999_pif_uuid}")

    defer(cleanup)

    host.ssh(f"interface-rename --update eth999='{eth1_mac}'")
    host.ssh("xe pif-scan host-uuid=$(xe host-list --minimal)")

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

    new_iface = host.ssh(f"xe pif-list MAC={eth1_mac} params=device --minimal")

    if reset_nics:
        assert new_iface == "eth1", "The management interface has NOT changed from eth999 to eth1"
    else:
        assert new_iface == "eth999", f"The management interface has changed from eth999 to '{new_iface}'"


def emergency_network_reset(host: Host, reset_nics: bool, defer: Defer) -> None:
    from data import HOST_DEFAULT_PASSWORD

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

    send_keys(ENTER)
    wait_for_screen_to_stabilize()
    debug_screen("Confirmation screen")
    assert has_content_on_screen("─ Emergency Network Reset ─")
    assert has_content_on_screen("Press <Enter> to reset the network configuration.")

    send_keys(ENTER)
    wait_for_screen_to_stabilize()
    debug_screen("Reboot screen")
