import logging
import time

import paramiko
import pyte

BUFFER_READ_SIZE = 4096
logging.getLogger("paramiko").setLevel(logging.WARNING)


def show_screen(screen: pyte.Screen) -> str:
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


def customize_isolinux(channel: paramiko.Channel, dom_id: str, vmlinuz_config: str):
    channel.get_pty(term='vt100', width=80, height=24)
    command = f"xl console -t serial {dom_id}"
    logging.debug(f"Run command {command!r}")
    channel.exec_command(command.encode())
    channel.settimeout(30.0)

    # Prepare terminal emulation for grub
    isolinux_screen = pyte.Screen(columns=80, lines=24)
    isolinux_screen.define_charset("U", "(")
    isolinux_stream = pyte.ByteStream(isolinux_screen)
    isolinux_stream.select_other_charset("@")

    # Repeatedly send a sentinel until it is echoed on screen
    sentinel = "sentinel1 "
    for attempt in range(30):
        channel.send(sentinel.encode())
        time.sleep(1)
        while channel.recv_ready():
            isolinux_stream.feed(channel.recv(BUFFER_READ_SIZE))
        if any(sentinel in line for line in isolinux_screen.display):
            break
    else:
        raise RuntimeError(f"No sentinel on screen: \n{show_screen(isolinux_screen)}")
    logging.debug(f"Echo screen:\n{show_screen(isolinux_screen)}")

    # Force the boot prompt to appear
    channel.send(b"sentinel2\rmenu")
    while not any(line.startswith("boot: menu") for line in isolinux_screen.display):
        isolinux_stream.feed(channel.recv(BUFFER_READ_SIZE))
    logging.debug(f"Boot screen:\n{show_screen(isolinux_screen)}")

    # Wait for isolinux menu to appear
    channel.send(b"\r")
    logging.info("Wait for isolinux screen")
    while not any("Press [Tab] to edit" in line for line in isolinux_screen.display):
        isolinux_stream.feed(channel.recv(BUFFER_READ_SIZE))

    # Wait for isolinux to stabilize
    time.sleep(1)
    while channel.recv_ready():
        isolinux_stream.feed(channel.recv(BUFFER_READ_SIZE))
    logging.debug(f"Isolinux screen:\n{show_screen(isolinux_screen)}")

    # Enter edition screen and wait for grub to stabilize
    channel.send(b"\t")
    logging.info("Wait for edition screen to appear")
    while not any(line.startswith("> ") for line in isolinux_screen.display):
        isolinux_stream.feed(channel.recv(BUFFER_READ_SIZE))

    # Wait for edition screen to stabilize
    time.sleep(1)
    while channel.recv_ready():
        isolinux_stream.feed(channel.recv(BUFFER_READ_SIZE))
    logging.debug(f"Isolinux edition screen:\n{show_screen(isolinux_screen)}")

    # Extract configuration
    screen_content = "".join(isolinux_screen.display)
    assert ">" in screen_content
    _, command = screen_content.rsplit(">", maxsplit=1)
    command = command.strip()
    logging.debug(f"Original command was: {command}")

    # Prepare new command
    mboot, old_vmlinuz_config, image = command.split(" --- ")
    assert old_vmlinuz_config.startswith("/boot/vmlinuz")
    new_command = " --- ".join([mboot, vmlinuz_config, image])

    # Clear configuration
    channel.send(b"\x15\x01\x0b")

    # Wait for command to be deleted
    logging.info("Wait for command to be cleared")
    while ">".ljust(80) not in isolinux_screen.display:
        isolinux_stream.feed(channel.recv(BUFFER_READ_SIZE))
    time.sleep(1)
    while channel.recv_ready():
        isolinux_stream.feed(channel.recv(BUFFER_READ_SIZE))
    logging.debug(f"Isolinux screen after command deletion:\n{show_screen(isolinux_screen)}")

    # Send new command and wait for edition screen to stabilize
    channel.send(new_command.encode())
    logging.info("Wait for edition screen to stabilize")
    time.sleep(1)
    while channel.recv_ready():
        isolinux_stream.feed(channel.recv(BUFFER_READ_SIZE))
        time.sleep(1)
    logging.debug(f"Isolinux screen with new command:\n{show_screen(isolinux_screen)}")

    # Send 'Enter' to validate
    channel.send(b"\r")

    # Wait for loading message to make sure '\r' is recieved
    logging.info("Wait for isolinux boot message")
    while not any("Loading /boot/vmlinuz... ok" in line for line in isolinux_screen.display):
        isolinux_stream.feed(channel.recv(BUFFER_READ_SIZE))


def customize_grub(channel: paramiko.Channel, dom_id: str, vmlinuz_config: str):
    channel.get_pty(term='vt100', width=100, height=32)
    command = f"xl console -t serial {dom_id}"
    logging.debug(f"Run command {command!r}")
    channel.exec_command(command.encode())
    channel.settimeout(30.0)

    # Prepare terminal emulation for grub
    grub_screen = pyte.Screen(columns=100, lines=32)
    grub_screen.define_charset("U", "(")
    grub_stream = pyte.ByteStream(grub_screen)
    grub_stream.select_other_charset("@")

    # Wait for grub to appear
    logging.info("Wait for grub screen")
    while not any("`e' to edit the commands" in line for line in grub_screen.display):
        grub_stream.feed(channel.recv(BUFFER_READ_SIZE))

    # Wait for grub to stabilize
    time.sleep(1)
    while channel.recv_ready():
        grub_stream.feed(channel.recv(BUFFER_READ_SIZE))
    logging.debug(f"Grub screen:\n{show_screen(grub_screen)}")

    # Enter edition screen and wait for grub to stabilize
    channel.send(b"e")
    logging.info("Wait for edition screen to appear")
    time.sleep(1)
    while channel.recv_ready():
        grub_stream.feed(channel.recv(BUFFER_READ_SIZE))
    logging.debug(f"Grub edition screen:\n{show_screen(grub_screen)}")

    # Send:
    # - ctrl-n (x3): Next line
    # - ctrl-k: Kill from cursor to end-of-line
    # - vmlinuz configuration
    channel.send(b"\x0e\x0e\x0e\x0b    module2 " + vmlinuz_config.encode())

    # Wait for edition screen to stabilize
    logging.info("Wait for edition screen to stabilize")
    time.sleep(1)
    while channel.recv_ready():
        grub_stream.feed(channel.recv(BUFFER_READ_SIZE))
        time.sleep(1)
    logging.debug(f"Grub screen after edition:\n{show_screen(grub_screen)}")

    # Send ctrl-x: Save and boot
    channel.send(b"\x18")

    # Wait for the last grub screen to make sure ctrl-x is received
    logging.info("Wait for grub boot message")
    while not any("Booting a command list" in line for line in grub_screen.display):
        grub_stream.feed(channel.recv(BUFFER_READ_SIZE))
