from __future__ import annotations

import pytest

from lib.vm import VM

from typing import Generator

@pytest.fixture(scope='function')
def vm_with_vcpu_count(request: pytest.FixtureRequest, imported_vm: VM) -> Generator[VM, None, None]:
    """
    Clone the imported VM and set it up with a given vCPU count (default: 128).

    The count can be overridden via indirect parametrization:
        @pytest.mark.parametrize('vm_with_vcpu_count', [n], indirect=True)
    """
    vcpu_count = getattr(request, 'param', 128)
    vm = imported_vm.clone()
    assert vm.is_halted(), "The VM must not be running to set vCPU parameters"
    vm.param_set('platform', 'true', key='vcpu-unrestricted')
    vm.param_set('VCPUs-max', str(vcpu_count))
    vm.param_set('VCPUs-at-startup', str(vcpu_count))
    yield vm
    vm.destroy()
