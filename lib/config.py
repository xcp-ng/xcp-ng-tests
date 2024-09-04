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
    f"{x}-vm1": f"{x}-vm1-fa3455096d2dda86014572d527fcdf704f7d1334" for x in """
    install.test::Nested::boot_inst[bios-75-host1-iso-ext]
    install.test::Nested::boot_inst[bios-75-host2-iso-ext]
    install.test::Nested::boot_inst[bios-76-host1-iso-ext]
    install.test::Nested::boot_inst[bios-76-host2-iso-ext]
    install.test::Nested::boot_inst[bios-80-host1-iso-ext]
    install.test::Nested::boot_inst[bios-80-host2-iso-ext]
    install.test::Nested::boot_inst[bios-81-host1-iso-ext]
    install.test::Nested::boot_inst[bios-81-host2-iso-ext]
    install.test::Nested::boot_inst[bios-821.1-host1-iso-ext]
    install.test::Nested::boot_inst[bios-821.1-host2-iso-ext]
    install.test::Nested::boot_inst[bios-83b1-host1-iso-ext]
    install.test::Nested::boot_inst[bios-83b1-host2-iso-ext]
    install.test::Nested::boot_inst[bios-83b2-host1-iso-ext]
    install.test::Nested::boot_inst[bios-83b2-host2-iso-ext]
    install.test::Nested::boot_inst[bios-83nightly-host1-iso-ext]
    install.test::Nested::boot_inst[bios-83nightly-host1-iso-lvm]
    install.test::Nested::boot_inst[bios-83nightly-host2-iso-ext]
    install.test::Nested::boot_inst[bios-83nightly-host2-iso-lvm]
    install.test::Nested::boot_inst[bios-83rc1-host1-iso-ext]
    install.test::Nested::boot_inst[bios-83rc1-host2-iso-ext]
    install.test::Nested::boot_inst[bios-ch821.1-host1-iso-ext]
    install.test::Nested::boot_inst[bios-ch821.1-host2-iso-ext]
    install.test::Nested::boot_inst[bios-xs8-host1-iso-ext]
    install.test::Nested::boot_inst[bios-xs8-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-75-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-75-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-76-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-76-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-80-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-80-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-81-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-81-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-821.1-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-821.1-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-83b1-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-83b1-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-83b2-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-83b2-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-83nightly-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-83nightly-host1-iso-lvm]
    install.test::Nested::boot_inst[uefi-83nightly-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-83nightly-host2-iso-lvm]
    install.test::Nested::boot_inst[uefi-83rc1-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-83rc1-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-ch821.1-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-ch821.1-host2-iso-ext]
    install.test::Nested::boot_inst[uefi-xs8-host1-iso-ext]
    install.test::Nested::boot_inst[uefi-xs8-host2-iso-ext]
    """.split()
}
