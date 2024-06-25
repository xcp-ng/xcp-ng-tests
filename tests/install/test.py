import logging
import pytest

from lib import installer
from lib.installer import AnswerFile

from data import NETWORKS
assert "MGMT" in NETWORKS

@pytest.mark.dependency()
class TestNested:
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
             cd_vbd=dict(device="xvdd", userdevice="3"),
             vifs=[dict(index=0, network_uuid=NETWORKS["MGMT"])],
             ))
    @pytest.mark.answerfile(lambda: AnswerFile("INSTALL") \
                            .top_append(
                                {"TAG": "source", "type": "local"},
                                {"TAG": "primary-disk", "CONTENTS": "nvme0n1"},
                            ))
    @pytest.mark.installer_iso("xcpng-8.2.1-2023")
    def test_install(self, create_vms, iso_remaster):
        assert len(create_vms) == 1
        installer.perform_install(iso=iso_remaster, host_vm=create_vms[0])
