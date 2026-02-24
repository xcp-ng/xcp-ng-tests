import pytest

import json
import logging
import time

from lib.commands import SSHCommandFailed
from lib.common import safe_split, vm_image, wait_for
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR
from lib.vm import VM
from tests.storage import vdi_is_open

from .conftest import GROUP_NAME, LINSTOR_PACKAGE

from typing import Tuple

# Requirements:
# - two or more XCP-ng hosts >= 8.2 with additional unused disk(s) for the SR
# - access to XCP-ng RPM repository from the host

class TestLinstorSRCreateDestroy:
    """
    Tests that do not use fixtures that setup the SR or import VMs,
    because they precisely need to test SR creation and destruction,
    and VM import.
    """

    def test_create_sr_without_linstor(self, host, lvm_disks, provisioning_type, storage_pool_name):
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

    def test_create_and_destroy_sr(self, pool_with_linstor, provisioning_type, storage_pool_name):
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


def get_drbd_status(host: Host, resource: str):
    logging.debug("[%s] Fetching DRBD status for resource `%s`...", host, resource)
    return json.loads(host.ssh(["drbdsetup", "status", resource, "--json"]))

def get_corrupted_resources(host: Host, resource: str):
    return [
        (
            res.get("name", ""),
            conn.get("name", ""),
            peer.get("out-of-sync", 0),
        )
        for res in get_drbd_status(host, resource)
        for conn in res.get("connections", [])
        for peer in conn.get("peer_devices", [])
        if peer.get("out-of-sync", 0) > 0
    ]

def wait_drbd_sync(host: Host, resource: str):
    logging.info("[%s] Waiting for DRBD sync on resource `%s`...", host, resource)
    host.ssh(["drbdadm", "wait-sync", resource])


def get_vdi_volume_name_from_linstor(master: Host, vdi_uuid: str) -> str:
    result = master.ssh([
        "linstor-kv-tool",
        "--dump-volumes",
        "-g",
        f"xcp-sr-{GROUP_NAME}_thin_device"
    ])
    volumes = json.loads(result)
    for k, v in volumes.items():
        path = safe_split(k, "/")
        if len(path) < 4:
            continue
        uuid = path[2]
        data_type = path[3]
        if uuid == vdi_uuid and data_type == "volume-name":
            return v
    raise FileNotFoundError(f"Could not find matching linstor volume for `{vdi_uuid}`")


def get_vdi_host(pool: Pool, vdi_uuid: str, path: str) -> Host:
    for h in pool.hosts:
        result = h.ssh(["test", "-e", path], simple_output=False, check=False)
        if result.returncode == 0:
            return h
    raise FileNotFoundError(f"Could not find matching host for `{vdi_uuid}`")


@pytest.mark.usefixtures("linstor_sr")
class TestLinstorSR:
    @pytest.mark.quicktest
    def test_quicktest(self, linstor_sr, provisioning_type):
        try:
            linstor_sr.run_quicktest()
        except Exception:
            if provisioning_type == "thick":
                pytest.xfail(reason="Known failure for thick provisioning")
            raise # Let thin failures fail test
        else:
            if provisioning_type == "thick":
                pytest.fail("Expected failure for thick provisioning did not occur (XPASS)")

    def test_vdi_is_not_open(self, vdi_on_linstor_sr):
        assert not vdi_is_open(vdi_on_linstor_sr)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_linstor_sr):
        vm = vm_on_linstor_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_linstor_sr):
        vm = vm_on_linstor_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.fixture(scope='function')
    def host_and_corrupted_vdi_on_linstor_sr(self, host: Host, linstor_sr: SR, vm_on_linstor_sr_function: VM):
        vm: VM = vm_on_linstor_sr_function
        pool: Pool = host.pool
        master: Host = pool.master

        try:
            vdi_uuid: str = next((
                vdi.uuid for vdi in vm.vdis if vdi.sr.uuid == linstor_sr.uuid
            ))

            volume_name = get_vdi_volume_name_from_linstor(master, vdi_uuid)
            lv_path = f"/dev/{GROUP_NAME}/{volume_name}_00000"
            vdi_host = get_vdi_host(pool, vdi_uuid, lv_path)
            logging.info("[%s]: corrupting `%s`", host, lv_path)
            vdi_host.ssh([
                "dd",
                "if=/dev/urandom",
                f"of={lv_path}",
                "bs=4096",
                # Lower values seem to go undetected sometimes
                "count=10000"  # ~40MB
            ])
            yield vm, vdi_host, volume_name
        finally:
            logging.info("<< Destroy corrupted VDI")
            vm.destroy(verify=True)

    @pytest.mark.small_vm
    def test_resynchronization(
        self, host_and_corrupted_vdi_on_linstor_sr: Tuple[VM, Host, str]
    ):
        (vm, host, resource_name) = host_and_corrupted_vdi_on_linstor_sr
        hostname = host.hostname()

        try:
            other_host = next(
                next(h for h in host.pool.hosts if h.hostname() == conn.get("name", ""))
                for res in get_drbd_status(host, resource_name)
                for conn in res.get("connections", [])
                for peer in conn.get("peer_devices", [])
                if peer.get("peer-disk-state", "") == "UpToDate"
            )
            logging.info("Elected `%s` as peer for verification and repair", other_host)
        except StopIteration:
            pytest.fail("Could not find an UpToDate peer host")

        corrupted = None
        max_attempts = 3
        # Attempting several times since testing revealed `drbdadm verify` can be flaky
        for attempt in range(1, max_attempts + 1):
            logging.info("`drbdadm verify` attempt %d/%d", attempt, max_attempts)
            logging.info("[%s] Running DRBD verify for %s...", other_host, resource_name)
            other_host.ssh(["drbdadm", "verify", f"{resource_name}:{hostname}/0"])
            wait_drbd_sync(other_host, resource_name)

            corrupted_resources = get_corrupted_resources(other_host, resource_name)
            if not corrupted_resources:
                logging.warning("No corrupted resources found on attempt #%d", attempt)
                continue
            for res_name, peer_name, out_of_sync in corrupted_resources:
                if res_name == resource_name and peer_name == hostname:
                    corrupted = (res_name, peer_name, out_of_sync)
            if corrupted:
                break
        if not corrupted:
            pytest.fail(f"Failed to identify corrupted resource after {max_attempts} attempts")

        logging.info("Invalidating remote resource `%s`...", resource_name)
        other_host.ssh([
            "drbdadm", "invalidate-remote",
            f"{resource_name}:{hostname}/0",
            "--reset-bitmap=no"
        ])
        wait_drbd_sync(other_host, resource_name)
        if get_corrupted_resources(other_host, resource_name):
            pytest.fail("Corrupted resource did not get fixed")

        vm.start(on=host.uuid)
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_linstor_sr, host, linstor_sr):
        sr = linstor_sr
        vm = vm_on_linstor_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start(on=host.uuid)
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.reboot
    def test_linstor_missing(self, linstor_sr, host):
        sr = linstor_sr
        linstor_installed = True
        try:
            host.yum_remove(['python-linstor', 'linstor-client'])
            linstor_installed = False
            try:
                sr.scan()
                assert False, "SR scan should have failed"
            except SSHCommandFailed:
                logging.info("SR scan failed as expected.")
            host.reboot(verify=True)
            # give the host some time to try to attach the SR
            time.sleep(10)
            logging.info("Assert PBD not attached")
            assert not sr.all_pbds_attached()
            host.yum_install(['xcp-ng-linstor'])
            linstor_installed = True

            # Needed because the linstor driver is not in the xapi
            # sm-plugins list because xcp-ng-linstor RPM has been
            # removed by the `yum remove ...` call.
            host.restart_toolstack(verify=True)

            sr.plug_pbds(verify=True)
            sr.scan()
        finally:
            if not linstor_installed:
                host.yum_install([LINSTOR_PACKAGE])

    # *** End of tests with reboots

# --- Test diskless resources --------------------------------------------------

def _get_diskful_hosts(host, controller_option, sr_group_name, vdi_uuid):
    # TODO: If any resource is in a temporary creation state or unknown, then need to wait intelligently.
    attempt = 0
    retries = 3
    sleep_sec = 5

    while attempt < retries:
        try:
            # Get volume name from VDI UUID
            # "xcp/volume/{vdi_uuid}/volume-name": "{volume_name}"
            volume_name = host.ssh([
                "linstor-kv-tool", "--dump-volumes", "-g", sr_group_name,
                "|", "grep", "volume-name", "|", "grep", f"/{vdi_uuid}/"
            ]).split(': ')[1].split('"')[1]

            # Find host where volume is UpToDate
            # | {volume_name} | {host} | 7017 | Unused | Ok    |   UpToDate | 2023-10-24 18:52:05 |
            lines = host.ssh([
                "linstor", controller_option, "resource", "list",
                "|", "grep", volume_name, "|", "grep", "UpToDate"
            ]).splitlines()
            diskfuls = []
            for line in lines:
                hostname = line.split('|')[2].strip()
                diskfuls += hostname
            return diskfuls
        except SSHCommandFailed as e:
            logging.error("SSH Command Failed (attempt %d/%d): %s", attempt + 1, retries, e)
            attempt += 1
            if attempt >= retries:
                raise
            time.sleep(sleep_sec)

def _ensure_resource_remain_diskless(host, controller_option, sr_group_name, vdi_uuid, diskless):
    diskfuls = _get_diskful_hosts(host, controller_option, sr_group_name, vdi_uuid)
    for diskless_host in diskless:
        assert diskless_host.param_get("name-label").lower() not in diskfuls

class TestLinstorDisklessResource:
    @pytest.mark.small_vm
    def test_diskless_kept(self, host, linstor_sr, vm_on_linstor_sr, storage_pool_name):
        vm = vm_on_linstor_sr
        vdi_uuids = vm.vdi_uuids(sr_uuid=linstor_sr.uuid)
        vdi_uuid = vdi_uuids[0]
        assert vdi_uuid is not None

        controller_option = "--controllers="
        for member in host.pool.hosts:
            controller_option += f"{member.hostname_or_ip},"

        sr_group_name = "xcp-sr-" + storage_pool_name.replace("/", "_")
        diskfuls = _get_diskful_hosts(host, controller_option, sr_group_name, vdi_uuid)
        diskless = []
        for member in host.pool.hosts:
            if member.param_get("name-label").lower() not in diskfuls:
                diskless += [member]
        assert diskless

        # Start VM on host with diskless resource
        vm.start(on=diskless[0].uuid)
        vm.wait_for_os_booted()
        _ensure_resource_remain_diskless(host, controller_option, sr_group_name, vdi_uuid, diskless)

        vm.shutdown(verify=True)
        _ensure_resource_remain_diskless(host, controller_option, sr_group_name, vdi_uuid, diskless)
