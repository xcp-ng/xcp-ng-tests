import pytest

import json
import logging
import shlex
import time

from lib.commands import SSHCommandFailed
from lib.common import Defer, safe_split, vm_image, wait_for
from lib.host import Host
from lib.pool import Pool
from lib.sr import SR
from lib.vdi import VDI
from lib.vm import VM
from tests.storage import vdi_is_open
from tests.storage.storage import check_critical_journal_revert, check_vdi_revert, check_vdi_revert_journal

from .conftest import GROUP_NAME, LINSTOR_PACKAGE

from typing import Tuple

# Requirements:
# - two or more XCP-ng hosts >= 8.2 with additional unused disk(s) for the SR
# - access to XCP-ng RPM repository from the host


def get_drbd_status(host: Host, resource: str):
    logging.debug("[%s] Fetching DRBD status for resource `%s`...", host, resource)
    return json.loads(host.ssh(shlex.join(["drbdsetup", "status", resource, "--json"])))

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
    host.ssh(shlex.join(["drbdadm", "wait-sync", resource]))


def get_vdi_volume_name_from_linstor(master: Host, vdi_uuid: str) -> str:
    result = master.ssh(shlex.join([
        "linstor-kv-tool",
        "--dump-volumes",
        "-g",
        f"xcp-sr-{GROUP_NAME}_thin_device"
    ]))
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
        result = h.ssh(shlex.join(["test", "-e", path]), simple_output=False, check=False)
        if result.returncode == 0:
            return h
    raise FileNotFoundError(f"Could not find matching host for `{vdi_uuid}`")


@pytest.mark.usefixtures("linstor_sr")
class TestLinstorSR:
    @pytest.mark.quicktest
    def test_quicktest(self, linstor_sr: SR, provisioning_type: str) -> None:
        try:
            linstor_sr.run_quicktest()
        except Exception:
            if provisioning_type == "thick":
                pytest.xfail(reason="Known failure for thick provisioning")
            raise # Let thin failures fail test
        else:
            if provisioning_type == "thick":
                pytest.fail("Expected failure for thick provisioning did not occur (XPASS)")

    def test_vdi_is_not_open(self, vdi_on_linstor_sr: VDI) -> None:
        assert not vdi_is_open(vdi_on_linstor_sr)

    @pytest.mark.small_vm # run with a small VM to test the features
    @pytest.mark.big_vm # and ideally with a big VM to test it scales
    def test_start_and_shutdown_VM(self, vm_on_linstor_sr: VM) -> None:
        vm = vm_on_linstor_sr
        vm.start()
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_snapshot(self, vm_on_linstor_sr: VM) -> None:
        vm = vm_on_linstor_sr
        vm.start()
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.fixture(scope='function')
    def host_and_vm_with_corrupted_vdi_on_linstor_sr(self, host: Host, linstor_sr: SR, vm_on_linstor_sr_function: VM):
        vm: VM = vm_on_linstor_sr_function
        pool: Pool = host.pool
        master: Host = pool.master

        vdi_uuid: str = next((
            vdi.uuid for vdi in vm.vdis if vdi.sr.uuid == linstor_sr.uuid
        ))

        volume_name = get_vdi_volume_name_from_linstor(master, vdi_uuid)
        lv_path = f"/dev/{GROUP_NAME}/{volume_name}_00000"
        vdi_host = get_vdi_host(pool, vdi_uuid, lv_path)
        logging.info("[%s]: corrupting `%s`", host, lv_path)
        vdi_host.ssh(shlex.join([
            "dd",
            "if=/dev/urandom",
            f"of={lv_path}",
            "bs=4096",
            # Lower values seem to go undetected sometimes
            "count=10000"  # ~40MB
        ]))
        yield vdi_host, vm, volume_name

    @pytest.mark.small_vm
    def test_resynchronization(
        self, host_and_vm_with_corrupted_vdi_on_linstor_sr: Tuple[Host, VM, str]
    ):
        (host, vm, resource_name) = host_and_vm_with_corrupted_vdi_on_linstor_sr
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
            other_host.ssh(shlex.join(["drbdadm", "verify", f"{resource_name}:{hostname}/0"]))
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
        other_host.ssh(shlex.join([
            "drbdadm", "invalidate-remote",
            f"{resource_name}:{hostname}/0",
            "--reset-bitmap=no"
        ]))
        wait_drbd_sync(other_host, resource_name)
        if get_corrupted_resources(other_host, resource_name):
            pytest.fail("Corrupted resource did not get fixed")

        vm.start(on=host.uuid)
        try:
            vm.wait_for_os_booted()
            vm.test_snapshot_on_running_vm()
        finally:
            vm.shutdown(verify=True)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_revert(self, vm_on_linstor_sr: VM, defer: Defer) -> None:
        check_vdi_revert(defer, vm_on_linstor_sr)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    @pytest.mark.parametrize(
        "fistpoint",
        [
            "LinstorSR_revert_create_insert",
            "LinstorSR_revert_create_src",
            "LinstorSR_revert_create_dest",
        ]
    )
    def test_revert_journal(self, vm_on_linstor_sr: VM, defer: Defer, exit_on_fistpoint: None, fistpoint: str):
        check_vdi_revert_journal(defer, vm_on_linstor_sr, fistpoint, vm_on_linstor_sr.host.pool.master)

    @pytest.mark.small_vm
    @pytest.mark.big_vm
    def test_critical_journal_revert(
        self, vm_on_linstor_sr: VM, defer: Defer, exit_on_fistpoint: None, hostA2: Host, linstor_no_monitor: None
    ) -> None:
        check_critical_journal_revert(defer, vm_on_linstor_sr, hostA2, "LinstorSR_revert_create_src")

    # *** tests with reboots (longer tests).

    @pytest.mark.reboot
    @pytest.mark.small_vm
    def test_reboot(self, vm_on_linstor_sr: VM, host: Host, linstor_sr: SR) -> None:
        sr = linstor_sr
        vm = vm_on_linstor_sr
        host.reboot(verify=True)
        wait_for(sr.all_pbds_attached, "Wait for PBD attached")
        # start the VM as a way to check that the underlying SR is operational
        vm.start(on=host.uuid)
        vm.wait_for_os_booted()
        vm.shutdown(verify=True)

    @pytest.mark.reboot
    def test_linstor_missing(self, linstor_sr: SR, host: Host) -> None:
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

def _get_diskful_hosts(host: Host, controller_option: str, sr_group_name: str, vdi_uuid: str) -> list[str]:
    # TODO: If any resource is in a temporary creation state or unknown, then need to wait intelligently.
    attempt = 0
    retries = 3
    sleep_sec = 5

    while attempt < retries:
        try:
            # Get volume name from VDI UUID
            # "xcp/volume/{vdi_uuid}/volume-name": "{volume_name}"
            volume_name = host.ssh(
                f'linstor-kv-tool --dump-volumes -g {sr_group_name} | grep volume-name | grep /{vdi_uuid}/'
            ).split(': ')[1].split('"')[1]

            # Find host where volume is UpToDate
            # | {volume_name} | {host} | 7017 | Unused | Ok    |   UpToDate | 2023-10-24 18:52:05 |
            lines = host.ssh(
                f'linstor {controller_option} resource list | grep {volume_name} | grep UpToDate'
            ).splitlines()
            diskfuls = []
            for line in lines:
                hostname = line.split('|')[2].strip()
                diskfuls += [hostname]
            return diskfuls
        except SSHCommandFailed as e:
            logging.error("SSH Command Failed (attempt %d/%d): %s", attempt + 1, retries, e)
            attempt += 1
            if attempt >= retries:
                raise
            time.sleep(sleep_sec)
    return []

def _ensure_resource_remain_diskless(
    host: Host, controller_option: str, sr_group_name: str, vdi_uuid: str, diskless: list[Host]
) -> None:
    diskfuls = _get_diskful_hosts(host, controller_option, sr_group_name, vdi_uuid)
    for diskless_host in diskless:
        assert diskless_host.name().lower() not in diskfuls

class TestLinstorDisklessResource:
    @pytest.mark.small_vm
    def test_diskless_kept(
        self, host: Host, linstor_sr: SR, linstor_redundancy: int, vm_on_linstor_sr: VM, storage_pool_name: str
    ) -> None:
        if len(linstor_sr.pool.hosts) <= linstor_redundancy:
            pytest.skip("This test requires at least one DRBD diskless")

        # 1. Prepare options.
        controller_option = "--controllers="
        for member in host.pool.hosts:
            controller_option += f"{member.hostname_or_ip},"

        sr_group_name = "xcp-sr-" + storage_pool_name.replace("/", "_")

        # 2. Get VM VDI.
        vm = vm_on_linstor_sr
        vdi = vm.vdis[0]

        # 3. Create a snap to ensure VDI cannot be coalesced during diskless checks.
        # To be more clear: if a coalesce is executed on the leaf, the VDI path is modified,
        # and we must prevent this situation otherwise we can't compare diskless state
        # between VM running and stopped.
        snap = vdi.snapshot()

        try:
            # 4. Fetch DRBD diskless.
            diskfuls = _get_diskful_hosts(host, controller_option, sr_group_name, vdi.uuid)
            diskless = []
            for member in host.pool.hosts:
                if member.name().lower() not in diskfuls:
                    diskless += [member]
            assert diskless

            # 5. Verify diskless state after VM boot and shutdown.
            vm.start(on=diskless[0].uuid)
            vm.wait_for_os_booted()
            _ensure_resource_remain_diskless(host, controller_option, sr_group_name, vdi.uuid, diskless)

            vm.shutdown(verify=True)
            _ensure_resource_remain_diskless(host, controller_option, sr_group_name, vdi.uuid, diskless)
        finally:
            snap.destroy()
