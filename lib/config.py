ignore_ssh_banner = False
ssh_output_max_lines = 20

def sr_device_config(datakey, *, required=[]):
    import data # import here to avoid depending on this user file for collecting tests
    config = getattr(data, datakey)
    for required_field in required:
        if required_field not in config:
            raise Exception(f"{datakey} lacks mandatory {required_field!r}")
    return config

# list obtained after running scripts/gen-ref-installs, from:
# $ xe vm-list params=name-description | grep $SHA | grep ::boot_inst | sort |
#   sed 's/.*Cache for \(.*\)-vm1-.*/\1/'

# FIXME no such cache yet
IMAGE_DEFAULT_EQUIVS = {
    f"{x}-vm1": f"{x}-vm1-38e791e0f61c6065f3c530d67b8f5f62bf748cf3" for x in """
    install.test::Nested::boot_inst[bios-81-host1-iso-ext]
    install.test::Nested::boot_inst[bios-81-host2-iso-ext]
    install.test::Nested::boot_inst[bios-821.1-host1-iso-ext]
    install.test::Nested::boot_inst[bios-821.1-host2-iso-ext]
    install.test::Nested::boot_inst[bios-830-host1-iso-ext]
    install.test::Nested::boot_inst[bios-830-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-81-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-81-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-821.1-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-821.1-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-830-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-830-host2-iso-ext]
    """.split()
}
