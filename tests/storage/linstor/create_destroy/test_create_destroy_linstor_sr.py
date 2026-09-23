from __future__ import annotations

import pytest

import logging

from lib.commands import SSHCommandFailed
from lib.common import safe_split, vm_image
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR

# Requirements:
# - two or more XCP-ng hosts >= 8.2 with additional unused disk(s) for the SR
# - access to XCP-ng RPM repository from the host

class TestLinstorSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_sr_without_linstor(
        self, host: Host, lvm_disks: None, provisioning_type: str, storage_pool_name: str
    ) -> None:
        # This test must be the first in the series in this module
        assert not host.is_package_installed('python-linstor'), \
            "linstor must not be installed on the host at the beginning of the tests"
        try:
            sr = host.sr_create('linstor', 'LINSTOR-SR-test', {
                'group-name': storage_pool_name,
                'redundancy': '1',
                'provisioning': provisioning_type
            }, shared=True)
            try:
                sr.destroy()
            except Exception:
                pass
            assert False, "SR creation should not have succeeded!"
        except SSHCommandFailed as e:
            logging.info("SR creation failed, as expected: {}".format(e))

    def test_create_and_destroy_sr(
        self, pool_with_linstor: Pool, provisioning_type: str, storage_pool_name: str
    ) -> None:
        # Create and destroy tested in the same test to leave the host as unchanged as possible
        master = pool_with_linstor.master
        sr = master.sr_create('linstor', 'LINSTOR-SR-test', {
            'group-name': storage_pool_name,
            'redundancy': '1',
            'provisioning': provisioning_type
        }, shared=True)
        # import a VM in order to detect vm import issues here rather than in the vm_on_linstor_sr fixture used in
        # the next tests, because errors in fixtures break teardown
        vm = master.import_vm(vm_image('mini-linux-x86_64-bios'), sr.uuid)
        vm.destroy(verify=True)
        sr.destroy(verify=True)

    def test_forget_and_introduce_sr(self, linstor_sr_ephemeral: SR):
        sr = linstor_sr_ephemeral
        sr_name = sr.param_get('name-label')
        all_pbds = sr.pbd_uuids()
        pbd_config_hosts: list[list[str]] = []
        pbd_config_devices: list[list[str]] = []
        for pbd in all_pbds:
            pbd_config_hosts.append(
                safe_split(sr.pool.master.xe('pbd-param-get', {'uuid': pbd, 'param-name': 'host-uuid'}))
            )
            pbd_config_devices.append(
                safe_split(sr.pool.master.xe('pbd-param-get', {'uuid': pbd, 'param-name': 'device-config'}))
            )

        sr.forget()
        logging.info(f"Forgot SR {sr.uuid} successfully")

        with pytest.raises(Exception):
            sr_type = sr.param_get('type') # Expecting exception as sr should not exist
            sr.plug_pbds() # Plug back pbds and let teardown handle SR destroy
            pytest.fail(f"SR still exists; returned type: {sr_type}")

        logging.info(f"Introducing SR {sr.uuid} back")
        new_sr = SR.introduce(sr.pool, type='linstor', shared=True, name_label=sr_name, sr_uuid=sr.uuid)

        # Example pbd_config_device
        # {provisioning: thin; redundancy: 3; group-name: linstor_group/thin_device}
        for pbd_config_host, pbd_config_device in zip(pbd_config_hosts, pbd_config_devices):
            pbd_config_dict = dict(
                (kv.split(": ")[0].strip(), kv.split(": ")[1].strip())
                for kv in pbd_config_device[0].split(";")
                if ": " in kv  # Ensure key-value pair
            )

            sr.pool.master.xe(
                "pbd-create",
                {
                    "sr-uuid": new_sr.uuid,
                    "host-uuid": pbd_config_host[0],
                    "content-type": "user",
                    "device-config": pbd_config_dict,
                },
            )

        new_sr.plug_pbds(verify=True)
        logging.info(f"Introduced SR {new_sr.uuid} successfully")
