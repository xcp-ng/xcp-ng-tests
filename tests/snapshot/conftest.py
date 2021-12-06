import logging
import pytest

@pytest.fixture(scope='module')
def vdis(host, local_sr_on_hostA1):
    def _make_vdi(name):
        return host.xe('vdi-create', {'name-label': name, 'virtual-size': '64', 'sr-uuid': sr.uuid})

    sr = local_sr_on_hostA1

    logging.info('> Creating VDIs')
    vdi_A, vdi_B, vdi_C = _make_vdi('VDI_A'), _make_vdi('VDI_B'), _make_vdi('VDI_C')

    yield vdi_A, vdi_B, vdi_C

    logging.info('< Destroying VDIs')
    for vdi in [vdi_A, vdi_B, vdi_C]:
        host.xe('vdi-destroy', {'uuid': vdi})

@pytest.fixture(scope='module')
def vm_with_vbds(host, vdis, imported_vm):
    def _make_vbd(host, device, vdi):
        host.xe('vbd-create', {
            'vm-uuid': vm.uuid, 'mode': 'RW', 'type': 'Disk', 'device': device, 'vdi-uuid': vdi
        })

    vm = imported_vm
    vdi_A, vdi_B, vdi_C = vdis

    for name, vdi in [("xvdn", vdi_A), ("xvdo", vdi_B), ("xvdp", vdi_C)]:
        _make_vbd(host, name, vdi)

    vm.start()
    yield vm
