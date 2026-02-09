import pytest

from lib.host import Host
from lib.snapshot import Snapshot
from lib.vm import VM

# Requirements:
# - an XCP-ng host (--hosts) >= 8.3
# - a VM (--vm) without xvd{n, o, p} device

def _orig_vdis_from_snapshot(host: Host, snapshot: Snapshot) -> list[str]:
    snap_vbds_str = snapshot.param_get('VBDs')
    snap_vbds = snap_vbds_str.split('; ')

    snap_vdis = list(map(
        lambda vbd: host.xe('vbd-param-get', {'uuid': vbd, 'param-name': 'vdi-uuid'}),
        snap_vbds
    ))
    snap_vdis = [vdi for vdi in snap_vdis if vdi != '<not in database>']

    return list(map(
        lambda vdi: host.xe('vdi-param-get', {'uuid': vdi, 'param-name': 'snapshot-of'}),
        snap_vdis
    ))

@pytest.mark.small_vm
def test_snapshot(host: Host, vdis: tuple[str, str, str], vm_with_vbds: VM) -> None:
    vm = vm_with_vbds

    snapshot = vm.snapshot()
    orig_vdis = _orig_vdis_from_snapshot(host, snapshot)

    for vdi in vdis:
        assert vdi in orig_vdis

    snapshot.destroy()

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
def test_snapshot_ignore_vdi(host: Host, vdis: tuple[str, str, str], vm_with_vbds: VM) -> None:
    vdi_A, vdi_B, vdi_C = vdis
    vm = vm_with_vbds

    snapshot = vm.snapshot(ignore_vdis=[vdi_B])
    orig_vdis = _orig_vdis_from_snapshot(host, snapshot)

    assert vdi_A in orig_vdis
    assert vdi_B not in orig_vdis
    assert vdi_C in orig_vdis

    snapshot.destroy()

@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
def test_snapshot_ignore_multiple_vdis(host: Host, vdis: tuple[str, str, str], vm_with_vbds: VM) -> None:
    vdi_A, vdi_B, vdi_C = vdis
    vm = vm_with_vbds

    snapshot = vm.snapshot(ignore_vdis=[vdi_B, vdi_C])
    orig_vdis = _orig_vdis_from_snapshot(host, snapshot)

    assert vdi_A in orig_vdis
    assert vdi_B not in orig_vdis
    assert vdi_C not in orig_vdis

    snapshot.destroy()
