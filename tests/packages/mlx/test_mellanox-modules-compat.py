import pytest

from lib.host import Host

# Requirements:
# From --hosts parameter:
# - host(A1): any master host of a pool, with access to XCP-ng RPM repositories.

MLX4_MODULE = 'mlx4_en'

def load_unload_mlx_module(host: Host) -> None:
    host.ssh(f'modprobe -v {MLX4_MODULE}')
    host.ssh(f'modprobe -r -v {MLX4_MODULE}')

@pytest.mark.usefixtures("host_without_mlx_card")
def test_install_mlx_modules_alt(host_without_mlx_compat_loaded: Host) -> None:
    host = host_without_mlx_compat_loaded

    # Ensure the modules are unloaded
    host.yum_remove(['mlx4-modules-alt', 'mellanox-mlnxen-alt'])

    # Start by loading mlx4
    host.yum_install(['mlx4-modules-alt'])
    load_unload_mlx_module(host)

    # Ensure that mlx_compat is still unloaded
    assert host.ssh_with_result('lsmod | grep mlx_compat').returncode == 1

    # Now load mellanox-mlnxen-alt
    host.yum_install(['mellanox-mlnxen-alt'])
    load_unload_mlx_module(host)
