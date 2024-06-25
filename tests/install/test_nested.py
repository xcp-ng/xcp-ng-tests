import logging
import pytest

@pytest.mark.vm_definitions(
    dict(name="vm1",
         template="Other install media",
         params=(
             # dict(param_name="", value=""),
             dict(param_name="memory-static-max", value="4GiB"),
             dict(param_name="memory-dynamic-max", value="4GiB"),
             dict(param_name="memory-dynamic-min", value="4GiB"),
             dict(param_name="platform", key="exp-nested-hvm", value="true"), # FIXME < 8.3 host?
             dict(param_name="HVM-boot-params", key="firmware", value="uefi"),
             dict(param_name="HVM-boot-params", key="order", value="dc"),
             dict(param_name="platform", key="device-model", value="qemu-upstream-uefi"),
         ),
         vdis=[dict(name="vm1 system disk", size="100GiB", device="xvda", userdevice="0")],
         vifs=[dict(index=0, network_uuid="eabc1038-e40f-2ae5-0781-a3adbec1cae8")], # FIXME
         ))
@pytest.mark.installer_iso("xcpng-8.2.1-2023")
def test_install(iso_remaster, create_vms):
    assert len(create_vms) == 1
    host_vm = create_vms[0]
    # FIXME should be part of vm def
    host_vm.create_cd_vbd(device="xvdd", userdevice="3")

    host_vm.insert_cd(iso_remaster)
