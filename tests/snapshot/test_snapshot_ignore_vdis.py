import pytest

# Requirements:
# - an XCP-ng host (--hosts) >= 8.3
# - a VM (--vm) without xvd{n, o, p} device

def _orig_vdis_from_snapshot(host, snapshot):
    snap_vbds = snapshot.param_get('VBDs')
    snap_vbds = snap_vbds.split('; ')

    snap_vdis = list(map(
        lambda vbd: host.xe('vbd-param-get', {'uuid': vbd, 'param-name': 'vdi-uuid'}),
        snap_vbds
    ))
    snap_vdis = [vdi for vdi in snap_vdis if vdi != '<not in database>']

    return list(map(
        lambda vdi: host.xe('vdi-param-get', {'uuid': vdi, 'param-name': 'snapshot-of'}),
        snap_vdis
    ))

@pytest.mark.small_vm # we just test the feature here, not that it scales
def test_snapshot(host, vdis, vm_with_vbds):
    vm = vm_with_vbds

    snapshot = vm.snapshot()
    orig_vdis = _orig_vdis_from_snapshot(host, snapshot)

    for vdi in vdis:
        assert vdi in orig_vdis

    snapshot.destroy()

@pytest.mark.small_vm # we just test the feature here, not that it scales
@pytest.mark.usefixtures("host_at_least_8_3")
def test_snapshot_ignore_vdi(host, vdis, vm_with_vbds):
    vdi_A, vdi_B, vdi_C = vdis
    vm = vm_with_vbds

    snapshot = vm.snapshot(ignore_vdis=[vdi_B])
    orig_vdis = _orig_vdis_from_snapshot(host, snapshot)

    assert vdi_A in orig_vdis
    assert vdi_B not in orig_vdis
    assert vdi_C in orig_vdis

    snapshot.destroy()

@pytest.mark.small_vm # we just test the feature here, not that it scales
@pytest.mark.usefixtures("host_at_least_8_3")
def test_snapshot_ignore_multiple_vdis(host, vdis, vm_with_vbds):
    vdi_A, vdi_B, vdi_C = vdis
    vm = vm_with_vbds

    snapshot = vm.snapshot(ignore_vdis=[vdi_B, vdi_C])
    orig_vdis = _orig_vdis_from_snapshot(host, snapshot)

    assert vdi_A in orig_vdis
    assert vdi_B not in orig_vdis
    assert vdi_C not in orig_vdis

    snapshot.destroy()
