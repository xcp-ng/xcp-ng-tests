ignore_ssh_banner = False
ssh_output_max_lines = 20

def sr_device_config(datakey):
    import data # import here to avoid depending on this user file for collecting tests
    return getattr(data, datakey)
