import pytest


@pytest.fixture
def vm_with_vcpu_count(request, imported_vm):
    """
    Set up the VM with a given vCPU count (default: 128).

    The count can be overridden via indirect parametrization:
        @pytest.mark.parametrize('vm_with_vcpu_count', [n], indirect=True)
    """
    vcpu_count = getattr(request, 'param', 128)
    vm = imported_vm
    assert not vm.is_running(), "VM must be halted before setting vCPU parameters"
    original_vcpus_max = vm.param_get('VCPUs-max')
    original_vcpus_at_startup = vm.param_get('VCPUs-at-startup')
    original_vcpu_unrestricted = vm.param_get('platform', 'vcpu-unrestricted', accept_unknown_key=True)
    vm.param_set('platform', 'true', key='vcpu-unrestricted')
    vm.param_set('VCPUs-max', str(vcpu_count))
    vm.param_set('VCPUs-at-startup', str(vcpu_count))
    yield vm
    vm.param_set('VCPUs-at-startup', original_vcpus_at_startup)
    vm.param_set('VCPUs-max', original_vcpus_max)
    if original_vcpu_unrestricted is None:
        vm.param_remove('platform', 'vcpu-unrestricted')
    else:
        vm.param_set('platform', original_vcpu_unrestricted, key='vcpu-unrestricted')
