import pytest
import logging

# Requirements:
# - one XCP-ng host

class TestUserDevice:
    def _check_iso(self, vm):
        return vm.ssh_with_result(["test", "-e", "/sys/class/block/sr0"]).returncode == 0

    def _get_vbd_iso(self, vm):
        return vm.host.xe("vbd-list", {"vm-uuid": vm.uuid, "device": "xvdd"}, minimal=True)

    def _set_userdevice(self, vm, vbd_uuid, userdevice):
        logging.info("Setting userdevice for VBD {} of VM {} to {}".format(vbd_uuid, vm.uuid, userdevice))
        vm.host.xe("vbd-param-set", {"userdevice": str(userdevice), "uuid": vbd_uuid})

    @pytest.mark.small_vm
    def test_sr0_exist(self, imported_vm):
        vm = imported_vm
        vm.start()
        vm.wait_for_os_booted()
        assert self._check_iso(vm)
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    def test_change_userdevice(self, started_vm):
        vm = started_vm

        vbd_uuid = self._get_vbd_iso(vm)
        vm.shutdown(verify=True)
        self._set_userdevice(vm, vbd_uuid, 4)
        vm.start()
        vm.wait_for_os_booted()

        assert self._check_iso(vm)
