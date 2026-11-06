# Packer: Alpine 3.23 Minimal UEFI for XCP-ng

Builds a minimal Alpine Linux 3.23.4 VM image (XVA, zstd-compressed) for use
in xcp-ng-tests. The image is UEFI-booted, has the XCP-ng guest tools
installed, and allows root SSH access using the xcp-ng CI public keys.

## VM Specs

| Property    | Value                        |
|-------------|------------------------------|
| OS          | Alpine Linux 3.23.4 (x86_64) |
| vCPUs       | 1                            |
| RAM         | 256 MiB                      |
| Disk        | 512 MiB                      |
| Boot        | UEFI (grub + GPT)            |
| Network     | DHCP (eth0)                  |
| Format      | XVA (zstd)                   |
| Guest tools | xe-guest-utilities (apk)     |

## Prerequisites

### On the machine running Packer

- [Packer](https://developer.hashicorp.com/packer) (tested with v1.x)
- The `xenserver-iso` builder plugin from
  [xenserver/packer-plugin-xenserver](https://github.com/xenserver/packer-plugin-xenserver)
- Python 3 with `libarchive-c==5.3` (for zstd post-processing):

  ```sh
  pip install 'libarchive-c==5.3'
  ```

### On the XCP-ng host

- An ISO SR where Packer can upload the Alpine ISO (the plugin uploads it
  automatically if you use `iso_url`)
- A network accessible from the build machine; the default variable value is
  `Pool-wide network associated with eth0` — override with `network_name` if
  needed

## Building

```sh
cd packer/
packer init alpine-3.23-uefi.pkr.hcl
packer build \
  -var xen_host=<XCP-ng IP or hostname> \
  -var xen_password=<XCP-ng root password> \
  alpine-3.23-uefi.pkr.hcl
```

Optional overrides:

```sh
  -var xen_username=root           # default: root
  -var network_name="<name>"       # default: Pool-wide network associated with eth0
  -var root_password=<password>    # temporary build password; default: packer
```

The output XVA is written to:

```
output-alpine-3.23-uefi/alpine-3.23-uefi.xva
```

After the build the post-processor rewrites it in-place with zstd compression
and sets the bridge to `xenbr0`.

## Directory structure

```
packer/
├── alpine-3.23-uefi.pkr.hcl  # Packer build definition (HCL)
├── http_files/
│   └── answers.txt           # Alpine setup-alpine answerfile
└── scripts/
    └── cleanup.sh            # Post-install cleanup run inside the VM
```

## Notes

- The Alpine guest tools ISO `install.sh` script does not support Alpine;
  `xe-guest-utilities` is installed from the Alpine community repository
  instead.
- `ROOTSSHKEY` in `answers.txt` injects the xcp-ng CI public keys during
  installation, so no separate provisioner step is needed for SSH keys.
- `USE_EFI=1` is passed on the kernel cmdline via `boot_command`; this makes
  `setup-disk` use grub and a GPT layout automatically.
- Swap is disabled (`SWAP_SIZE=0`) to keep the image within 512 MiB.

## Troubleshooting

**Boot hangs / VNC never gets past the boot prompt**
: Increase `boot_wait` in the JSON (default `10s`) and ensure VNC is
  reachable from the Packer host.

**SSH timeout**
: The install takes up to ~3 minutes on a busy host. Increase
  `ssh_wait_timeout` if needed.

**Disk full during install**
: If `setup-disk` fails due to space, the disk size can be bumped to 1024 MB
  by passing `-var disk_size=1024` — but note that the builder does not expose
  this directly; you would need to edit the JSON.

**xva_bridge.py fails**
: Ensure `libarchive-c==5.3` is installed. Other versions may not work due to
  internal API usage.
