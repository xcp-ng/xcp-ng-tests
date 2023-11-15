import pytest

# Requirements:
# From --hosts parameter:
# - host(A1): first XCP-ng host >= 8.2 with an additional unused disk for the SR.
# From --vm parameter
# - A VM to import to the LINSTOR SR
# And:
# - access to XCP-ng RPM repository from hostA1

def _get_diskful_hosts(host, controller_option, volume_name):
    # Find host where volume is diskless
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

def _ensure_resource_remain_diskless(host, controller_option, volume_name, diskless):
    diskfuls = _get_diskful_hosts(host, controller_option, volume_name)
    for diskless_host in diskless:
        assert diskless_host.param_get("name-label").lower() not in diskfuls

class TestLinstorDisklessResource:
    @pytest.mark.small_vm
    def test_diskless_kept(self, host, linstor_sr, vm_on_linstor_sr):
        vm = vm_on_linstor_sr
        vdi_uuids = vm.vdi_uuids(sr_uuid=linstor_sr.uuid)
        vdi_uuid = vdi_uuids[0]
        assert vdi_uuid is not None

        controller_option = "--controllers="
        for member in host.pool.hosts:
            controller_option += f"{member.hostname_or_ip},"

        # Get volume name from VDI uuid
        # "xcp/volume/{vdi_uuid}/volume-name": "{volume_name}"
        output = host.ssh([
            "linstor-kv-tool", "--dump-volumes", "-g", "xcp-sr-linstor_group_thin_device",
            "|", "grep", "volume-name", "|", "grep", vdi_uuid
        ])
        volume_name = output.split(': ')[1].split('"')[1]

        diskfuls = _get_diskful_hosts(host, controller_option, volume_name)
        diskless = []
        for member in host.pool.hosts:
            if member.param_get("name-label").lower() not in diskfuls:
                diskless += [member]
        assert diskless

        # Start VM on host with diskless resource
        vm.start(on=diskless[0].uuid)
        vm.wait_for_os_booted()
        _ensure_resource_remain_diskless(host, controller_option, volume_name, diskless)

        vm.shutdown(verify=True)
        _ensure_resource_remain_diskless(host, controller_option, volume_name, diskless)
