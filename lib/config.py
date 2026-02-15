

ignore_ssh_banner = False
ssh_output_max_lines = 20

def sr_device_config(datakey: str, *, required: list[str] = []) -> dict[str, str]:
    import data  # import here to avoid depending on this user file for collecting tests
    config = getattr(data, datakey)
    for required_field in required:
        if required_field not in config:
            raise Exception(f"{datakey} lacks mandatory {required_field!r}")
    return config
