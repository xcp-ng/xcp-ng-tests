from lib.common import GiB

ignore_ssh_banner = False
ssh_output_max_lines = 20
volume_size = 1 * GiB
write_volume_cap = 2 * GiB
write_volume_align = 1
blktap_max_duration = 30
blktap_destroy_iterations = 10
blktap_bench_outdir: str | None = None
blktap_bench_runtime = 30
blktap_bench_matrix = 'short'
blktap_bench_image_dir = '/tmp'
blktap_bench_image_size = 4096
blktap_polling_duration: int | None = None
blktap_polling_idle_threshold: int | None = None

def sr_device_config(datakey: str, *, required: list[str] = []) -> dict[str, str]:
    import data  # import here to avoid depending on this user file for collecting tests
    config = getattr(data, datakey)
    for required_field in required:
        if required_field not in config:
            raise Exception(f"{datakey} lacks mandatory {required_field!r}")
    return config
