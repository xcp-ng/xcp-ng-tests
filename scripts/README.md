# Overview

This directory contains utility scripts for XCP-ng test automation.

# capture-console.py

Captures VM console screenshots from XO-lite (default) or XOA, via VNC over WebSocket.
The script creates a local WebSocket-to-TCP proxy that handles authentication (either XAPI session tokens
or browser cookies) and connects to the VM's VNC console.
It then uses asyncvnc2 to capture a screenshot and save it as a PNG file.

## XOLite Mode (XAPI Session Authentication)

Used for capturing console screenshots from XCP-ng hosts directly via XAPI.

**Usage:**
```bash
python3 capture-console.py --host <ip> --vm-uuid <uuid> [output_file] [options]
```

**Examples:**
```bash
# Using environment variables for credentials (default filename: screenshot.png)
export HOST_DEFAULT_USER="root"
export HOST_DEFAULT_PASSWORD="password"
python3 capture-console.py --host 192.168.1.100 --vm-uuid a1b2c3d4-1234-5678-90ab-cdef12345678

# Custom filename
python3 capture-console.py --host 192.168.1.100 --vm-uuid a1b2c3d4-1234-5678-90ab-cdef12345678 \
  my_screenshot.png

# Custom filename with output directory
python3 capture-console.py --host 192.168.1.100 --vm-uuid a1b2c3d4-1234-5678-90ab-cdef12345678 \
  vm_console.png --output-dir /tmp/logs

# Using command-line options to override credentials
python3 capture-console.py --host 192.168.1.100 --vm-uuid a1b2c3d4-1234-5678-90ab-cdef12345678 \
  --user root --password mypassword --output-dir /tmp/logs
```

## XOA Mode (Cookie-Based Authentication)

Used for capturing console screenshots from Xen Orchestra Appliance (XOA) web interface.
You must supply the cookie, that you can get from your browser for instance.

**Usage:**
```bash
python3 capture-console.py <websocket_url> [output_file] --cookie <cookie>
```

**Example:**
```bash
python3 capture-console.py \
  wss://xoa.example.com/api/consoles/0377d240-dcd5-bfe0-f2ea-878853f8f1dc \
  screenshot.png \
  --cookie "connect.sid=s%3AK2UCcIuGdDd0...; clientId=pjbvcidrbta; token=198jIZV5rjyQfQ..."
```

## BUILD_NUMBER *(Jenkins-specific)*
Build number assigned by Jenkins CI will be used to name the default directory, following that scheme:
`/tmp/pytest-logs/session_{timestamp}_build_{BUILD_NUMBER}/`

# extract_logs.py

# install_xcpng.py

# get_xva_bridge.sh / set_xva_bridge.sh

# xcpng-fs-diff.py

