import logging

from lib.host import Host

from typing import Final

FISTPOINT_DIR: Final = "/tmp"
LVHDRT_EXIT_FIST: Final = "fist_LVHDRT_exit"


class FistPoint:
    """
    A fistpoint is an action that you can enable in the smapi for tests.

    It allows for example, add a sleep at some point or raise an exception.
    For example:
    ```
    with FistPoint(vm.host, "blktap_activate_inject_failure"):
        with pytest.raises(SSHCommandFailed):
            vm.start()
            vm.shutdown(force=True)
    ```
    Activating the fistpoint `blktap_activate_inject_failure` mean that the VDI
    activation will fail. This fistpoint always raise an exception but most
    fistpoint just add a sleep at a point in the code.
    Using the fixture `exit_on_fistpoint` make all fistpoints raise an
    exception instead by enabling a special fistpoint called `fist_LVHDRT_exit`
    """

    fistpointName: str

    def __init__(self, host: Host, name: str):
        self.fistpointName = self._get_name(name)
        self.host = host

    @staticmethod
    def enable_exit_on_fistpoint(host: Host):
        host.create_file(FistPoint._get_path(LVHDRT_EXIT_FIST), "")

    @staticmethod
    def disable_exit_on_fistpoint(host: Host):
        host.ssh(["rm", FistPoint._get_path(LVHDRT_EXIT_FIST)])

    @staticmethod
    def _get_name(name: str) -> str:
        if name.startswith("fist_"):
            return name
        else:
            return f"fist_{name}"

    @staticmethod
    def _get_path(name) -> str:
        return f"{FISTPOINT_DIR}/{name}"

    def enable(self):
        logging.info(f"Enable fistpoint {self.fistpointName}")
        self.host.create_file(self._get_path(self.fistpointName), "")

    def disable(self):
        logging.info(f"Disabling fistpoint {self.fistpointName}")
        self.host.ssh(["rm", self._get_path(self.fistpointName)])

    def __enter__(self):
        self.enable()
        return self

    def __exit__(self, *_):
        self.disable()
