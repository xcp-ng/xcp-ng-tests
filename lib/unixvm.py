from __future__ import annotations

import logging

from lib.vm import VM

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.host import Host


class UnixVM(VM):

    def __init__(self, uuid: str, host: 'Host'):
        super().__init__(uuid, host)

    @classmethod
    def detect_distro(cls, vm: VM) -> str:
        # Try VM's built-in distro() method first
        distro = vm.distro()
        if distro:
            return str(distro).strip()

        # Fallback: read /etc/os-release or check for centos-release
        result = vm.ssh(
            '([ -e /etc/os-release ] && . /etc/os-release && echo $ID) ||'
            ' ([ -e /etc/centos-release ] && echo centos)',
            check=False
        )
        if isinstance(result, bytes):
            return result.decode('utf-8').strip()
        return str(result).strip()

    @classmethod
    def get_distro_class(cls, distro: str) -> type['UnixVM']:
        # Import here to avoid circular dependencies
        from lib.distros import (
            AlmaVM,
            AlpineVM,
            CentOSVM,
            DebianVM,
            FreeBsdVM,
            OpenSuseVM,
            UbuntuVM,
        )

        distro_map = {
            'alpine': AlpineVM,
            'almalinux': AlmaVM,
            'centos': CentOSVM,
            'debian': DebianVM,
            'freebsd': FreeBsdVM,
            'opensuse-leap': OpenSuseVM,
            'ubuntu': UbuntuVM,
        }

        if distro not in distro_map:
            raise ValueError(f"Unknown distro: {distro}")

        return distro_map[distro]

    @classmethod
    def from_vm(cls, vm: VM) -> 'UnixVM':
        instance = cls(vm.uuid, vm.host)
        # Copy runtime state from the original VM
        instance.ip = vm.ip
        instance.previous_host = vm.previous_host
        instance.is_windows = vm.is_windows
        instance.is_uefi = vm.is_uefi
        if hasattr(vm, 'vdis'):
            instance.vdis = vm.vdis
        return instance

    @classmethod
    def from_vm_auto_detect(cls, vm: VM) -> 'UnixVM':
        distro = cls.detect_distro(vm)
        logging.debug(f"Detected distro: {distro}")
        distro_class = cls.get_distro_class(distro)
        return distro_class.from_vm(vm)

    def configure_serial_console(self) -> None:
        raise NotImplementedError(
            f"Serial console configuration not implemented for {self.__class__.__name__}"
        )
