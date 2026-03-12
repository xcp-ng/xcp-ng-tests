# XCP-ng Server Compatibility Test Kit

## Requirements

- **Docker** installed on your machine (or Docker Desktop for macOS/Windows).
- **Hosts to test:**
  - At least one x86_64 server with XCP-ng installed (the release you want to test),
    [updated with the latest updates](https://xcp-ng.org/docs/updates.html).
  - A disk for the system, and a separate, empty disk for testing.
  - Optionally, a second identical host. If provided, will be automatically
    joined to the first to form a pool for storage migration and live migration
    tests. **Both hosts must have the same SSH root password.**
  - Internet access to download the test image.
  - SSH access available (root user; password-based authentication).
- **Time:** The test suite execution might take up to an hour, depending on the hardware.

## Security Notes

The test VM is configured with a known access key and is **strictly reserved for testing purposes only**.

## Quick Start

Interactive mode (recommended):

```bash
docker run -it --rm -v ${PWD}:/app/logs:Z ghcr.io/xcp-ng/xcp-ng-tests/compat-kit
```

You will be prompted for:

- IP/hostname of the pool master.
- IP/hostname of a second host (optional; press Enter to skip).
- SSH root password (must be the same for both hosts).

Alternatively, provide arguments on the command line:

```bash
docker run --rm \
  -v ${PWD}:/app/logs \
  ghcr.io/xcp-ng/xcp-ng-tests/compat-kit \
  --master-host 192.168.1.10 \
  --second-host 192.168.1.11 \
  --password your_root_password
```

After the container completes, test log files will be in the current directory:

- `test_kit_1.log` — Single-host tests.
- `test_kit_2.log` — Two-host pool tests — only if a second host was provided.
- `test_kit_3.log` — Xen-level tests.

Open these files locally with your text editor or log viewer.

## Command-line options

```text
  --master-host MASTER_HOST
                        IP or hostname of the pool master
  --second-host SECOND_HOST
                        IP or hostname of the second host (optional, for pool tests)
  --password PASSWORD   SSH root password (if not provided, will be prompted)
  --log-dir LOG_DIR     Directory where log files will be written (default: /app/logs)
  --log-level {debug,info,warning,error,critical}
                        Logging level (default: info)
  --vm-image-url VM_IMAGE_URL
                        URL to the VM image to download (default: https://nextcloud.vates.tech/index.php/s/MmEjo8qYo7Ccs2B/download)
  --skip-cleanup        Don't remove SSH keys from hosts on exit
  --dry-run             Print what would be done without making any changes
  --non-interactive     Don't prompt for missing parameters; fail if required arguments are missing
```

## Development

### Building the Docker image

From the xcp-ng-tests repository root:

```bash
docker build -t ghcr.io/xcp-ng/xcp-ng-tests/compat-kit -f compat_kit/Dockerfile .
```

This creates an image with all dependencies pre-installed, including the xcp-ng-tests suite and
test runner tools.
