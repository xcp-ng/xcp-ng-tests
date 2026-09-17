from __future__ import annotations

import logging

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.installer import InstallerVM

def customize_isolinux(installer_vm: InstallerVM, vmlinuz_config: str) -> None:
    # Wait for the boot screen to appear
    installer_vm.wait_for_screen_content("boot:")
    installer_vm.send_to_console("menu")
    installer_vm.wait_for_screen_content("boot: menu")
    installer_vm.wait_for_screen_to_stabilize()
    installer_vm.debug_screen("Boot screen")

    # Wait for isolinux menu to appear
    installer_vm.send_to_console("\r")
    logging.info("Wait for isolinux screen")
    installer_vm.wait_for_screen_content("Press [Tab] to edit")
    installer_vm.wait_for_screen_to_settle()
    installer_vm.debug_screen("Isolinux screen")

    # Enter edition screen and wait for isolinux to stabilize
    installer_vm.send_to_console("\t")
    logging.info("Wait for edition screen to appear")
    installer_vm.wait_for_screen_content("> ")
    installer_vm.wait_for_screen_to_stabilize()
    installer_vm.debug_screen("Isolinux edition screen")

    # Extract configuration
    screen_content = "".join(installer_vm.console_screen.display)
    assert ">" in screen_content
    _, command = screen_content.rsplit(">", maxsplit=1)
    command = command.strip()
    logging.debug(f"Original command was: {command}")

    # Prepare new command
    mboot, old_vmlinuz_config, image = command.split(" --- ")
    assert old_vmlinuz_config.startswith("/boot/vmlinuz")
    new_command = " --- ".join([mboot, vmlinuz_config, image])

    # Clear configuration
    installer_vm.send_to_console("\x15\x01\x0b")

    # Wait for command to be deleted
    logging.info("Wait for command to be cleared")
    installer_vm.wait_for_screen_content(">".ljust(80))
    installer_vm.wait_for_screen_to_stabilize()
    installer_vm.debug_screen("Isolinux screen after command deletion")

    # Send new command and wait for edition screen to stabilize
    installer_vm.send_to_console(new_command)
    logging.info("Wait for edition screen to stabilize")
    installer_vm.wait_for_screen_to_stabilize()
    installer_vm.debug_screen("Isolinux screen with new command")

    # Send 'Enter' to validate
    installer_vm.send_to_console("\r")

    # Wait for loading message to make sure '\r' is received
    logging.info("Wait for isolinux boot message")
    installer_vm.wait_for_screen_content("Loading /boot/vmlinuz... ok")

def customize_grub(installer_vm: InstallerVM, vmlinuz_config: str):
    # Wait for grub to appear
    logging.info("Wait for grub screen")
    installer_vm.wait_for_screen_content("`e' to edit the commands")
    installer_vm.wait_for_screen_to_settle()
    installer_vm.debug_screen("Grub screen")

    # Enter edition screen and wait for grub to stabilize
    installer_vm.send_to_console("e")
    logging.info("Wait for edition screen to appear")
    installer_vm.wait_for_screen_to_stabilize()
    installer_vm.debug_screen("Grub edition screen")

    # Send:
    # - ctrl-n (x3): Next line
    # - ctrl-k: Kill from cursor to end-of-line
    # - vmlinuz configuration
    installer_vm.send_to_console(f"\x0e\x0e\x0e\x0b    module2 {vmlinuz_config}")

    # Wait for edition screen to stabilize
    logging.info("Wait for edition screen to stabilize")
    installer_vm.wait_for_screen_to_stabilize()
    installer_vm.debug_screen("Grub screen after edition")

    # Send ctrl-x: Save and boot
    installer_vm.send_to_console("\x18")

    # Wait for the last grub screen to make sure ctrl-x is received
    logging.info("Wait for grub boot message")
    installer_vm.wait_for_screen_content("Booting a command list")

    # Resize the screen now that we're leaving grub
    installer_vm.console_screen.resize(columns=80, lines=24)
