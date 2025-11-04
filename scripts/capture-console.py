#!/usr/bin/env python3
"""
VNC WebSocket Proxy - Capture screen from VNC over WebSocket

This script acts as a WebSocket-to-TCP proxy for VNC connections, handling authentication
and proxying the connection to a local TCP port where a VNC library can connect.

Supports two modes:
1. XOA mode: Uses cookie-based authentication (--cookie)
2. XOLite mode: Uses XAPI session authentication (--host, --vm-uuid, --user, --password)

Usage:
  XOA:    python3 capture-console.py <websocket_url> [output_file] --cookie <cookie>
  XOLite: python3 capture-console.py --host <ip> --vm-uuid <uuid> [output_file] [--user <user>] [--password <pass>]

Environment variables:
  HOST_DEFAULT_USER: Default username for XAPI authentication (can be overridden by --user)
  HOST_DEFAULT_PASSWORD: Default password for XAPI authentication (can be overridden by --password)

Dependencies:
  - websockets: WebSocket client
  - asyncvnc2: VNC client library with ZRLE support
  - Pillow (PIL): Image processing
"""

import asyncio
import websockets
import sys
import ssl
import os
import json
import urllib.request
import argparse
import logging
import socket
import asyncvnc2
from PIL import Image


async def websocket_to_tcp_proxy(ws_url, cookie=None):

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    additional_headers = {'Cookie': cookie} if cookie else {}

    async def handle_client(reader, writer):

        logging.debug(f"Client connected from {writer.get_extra_info('peername')}")

        try:
            # Connect to WebSocket VNC server
            async with websockets.connect(
                ws_url,
                subprotocols=['binary'],
                ssl=ssl_context,
                additional_headers=additional_headers
            ) as websocket:
                logging.info(f"Connected to WebSocket VNC server: {ws_url}")

                async def ws_to_tcp():
                    async for message in websocket:
                        writer.write(message)
                        await writer.drain()
                        logging.debug(f"WS→TCP: {len(message)} bytes")

                async def tcp_to_ws():
                    while True:
                        data = await reader.read(8192)
                        if not data:
                            break
                        await websocket.send(data)
                        logging.debug(f"TCP→WS: {len(data)} bytes")

                # Run both directions concurrently until one completes
                await asyncio.gather(ws_to_tcp(), tcp_to_ws(), return_exceptions=True)

        except Exception as e:
            logging.debug(f"Proxy error: {e}")
        finally:
            logging.debug("Proxy stopped")
            writer.close()
            await writer.wait_closed()

    # Start TCP server with port 0 to let OS pick a random available port
    server = await asyncio.start_server(handle_client, '127.0.0.1', 0)

    local_port = server.sockets[0].getsockname()[1]

    logging.info(f"Proxy listening on 127.0.0.1:{local_port}")

    return local_port, server


async def capture_vnc_screenshot(local_port, output_file):

    logging.info(f"Connecting VNC client to 127.0.0.1:{local_port}")

    async with asyncvnc2.connect('127.0.0.1', local_port) as client:
        logging.info("VNC client connected, capturing screenshot...")

        pixels = await client.screenshot()

        image = Image.fromarray(pixels)
        image.save(output_file)
        logging.info(f"Screenshot saved to {output_file}")


def xapi_authenticate(host_ip, username, password):

    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "session.login_with_password",
        "params": [username, password]
    }

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        f"https://{host_ip}/jsonrpc",
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )

    with urllib.request.urlopen(req, context=ssl_context) as response:
        result = json.loads(response.read().decode('utf-8'))
        if 'result' in result:
            return result['result']
        raise Exception(f"XAPI authentication failed: {result.get('error', result)}")


async def main():
    # Create argument parser with custom usage message
    parser = argparse.ArgumentParser(
        description='VNC WebSocket Console Capture',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="""
  XOA mode (cookie-based authentication):
    %(prog)s <websocket_url> [output_file] --cookie <cookie>

  XOLite mode (XAPI session authentication):
    %(prog)s --host <ip> --vm-uuid <uuid> [output_file] [--user <user>] [--password <pass>]
""",
        epilog="""
Options:
  --cookie <cookie>       Cookie for XOA authentication
  --host <ip>             Host IP for XOLite mode
  --vm-uuid <uuid>        VM UUID for XOLite mode
  --user <user>           Username (default: HOST_DEFAULT_USER env var or 'root')
  --password <pass>       Password (default: HOST_DEFAULT_PASSWORD env var)
  --output-dir <dir>      Output directory for screenshot
  --verbose, -v           Enable verbose output

Examples:
  XOLite: %(prog)s --host 192.168.1.100 --vm-uuid abc-123-def screenshot.png
  XOA:    %(prog)s wss://xoa.example.com/api/consoles/xxx --cookie "..."

Environment variables:
  HOST_DEFAULT_USER       Default username for XAPI authentication
  HOST_DEFAULT_PASSWORD   Default password for XAPI authentication
        """
    )

    # Positional arguments
    parser.add_argument('url_or_filename', nargs='?',
                        help=argparse.SUPPRESS)  # Hidden - shown in usage
    parser.add_argument('filename', nargs='?',
                        help=argparse.SUPPRESS)  # Hidden - shown in usage

    # XOLite mode options
    parser.add_argument('--host',
                        help=argparse.SUPPRESS)  # Hidden - shown in epilog
    parser.add_argument('--vm-uuid',
                        help=argparse.SUPPRESS)  # Hidden - shown in epilog
    parser.add_argument('--user',
                        help=argparse.SUPPRESS)  # Hidden - shown in epilog
    parser.add_argument('--password',
                        help=argparse.SUPPRESS)  # Hidden - shown in epilog

    # XOA mode options
    parser.add_argument('--cookie',
                        help=argparse.SUPPRESS)  # Hidden - shown in epilog

    # Common options
    parser.add_argument('--output-dir', dest='output_dir',
                        help=argparse.SUPPRESS)  # Hidden - shown in epilog
    parser.add_argument('--verbose', '-v', action='store_true',
                        help=argparse.SUPPRESS)  # Hidden - shown in epilog

    args = parser.parse_args()

    # Configure logging based on verbose flag
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format='%(message)s')
    else:
        logging.basicConfig(level=logging.WARNING, format='%(message)s')

    # Determine mode and validate arguments
    if args.cookie:
        # XOA mode: cookie-based authentication
        if not args.url_or_filename:
            print("Error: WebSocket URL required for XOA mode")
            print()
            print("Usage: python3 capture-console.py <websocket_url> [output_file] --cookie <cookie>")
            sys.exit(1)

        ws_url = args.url_or_filename
        output_file = args.filename if args.filename else "screenshot.png"
        cookie = args.cookie
        logging.info("Using XOA mode (cookie-based authentication)")

    elif args.host and args.vm_uuid:
        # XOLite mode: XAPI session authentication
        logging.info("Using XOLite mode (XAPI session authentication)")

        # Get credentials from args or environment variables
        username = args.user if args.user else os.environ.get('HOST_DEFAULT_USER', 'root')
        password = args.password if args.password else os.environ.get('HOST_DEFAULT_PASSWORD', '')

        # Output filename from positional arg or default
        output_file = args.url_or_filename if args.url_or_filename else "screenshot.png"

        logging.info(f"Authenticating with XAPI at {args.host}...")
        try:
            session_id = xapi_authenticate(args.host, username, password)
            logging.info(f"Authentication successful, session ID: {session_id[:20]}...")
        except Exception as e:
            print(f"Authentication failed: {e}")
            sys.exit(1)

        # Construct WebSocket URL
        ws_url = f"wss://{args.host}/console?uuid={args.vm_uuid}&session_id={session_id}"
        cookie = None

    else:
        # Neither mode specified - show nice usage
        print("VNC WebSocket Console Capture")
        print()
        print("XOA mode (cookie-based authentication):")
        print("  python3 capture-console.py <websocket_url> [output_file] --cookie <cookie>")
        print()
        print("XOLite mode (XAPI session authentication):")
        print("  python3 capture-console.py --host <ip> --vm-uuid <uuid> [output_file]"
              " [--user <user>] [--password <pass>]")
        print()
        print("Options:")
        print("  --cookie <cookie>       Cookie for XOA authentication")
        print("  --host <ip>             Host IP for XOLite mode")
        print("  --vm-uuid <uuid>        VM UUID for XOLite mode")
        print("  --user <user>           Username (default: HOST_DEFAULT_USER env var or 'root')")
        print("  --password <pass>       Password (default: HOST_DEFAULT_PASSWORD env var)")
        print("  --output-dir <dir>      Output directory for screenshot")
        print("  --verbose, -v           Enable verbose output")
        print()
        print("Environment variables:")
        print("  HOST_DEFAULT_USER       Default username for XAPI authentication")
        print("  HOST_DEFAULT_PASSWORD   Default password for XAPI authentication")
        sys.exit(1)

    # If output directory is specified, prepend it to output_file
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        output_file = os.path.join(args.output_dir, output_file)

    try:
        # Start WebSocket-to-TCP proxy (server is already serving)
        local_port, server = await websocket_to_tcp_proxy(ws_url, cookie)

        try:
            # Capture screenshot using VNC library with timeout
            await asyncio.wait_for(
                capture_vnc_screenshot(local_port, output_file),
                timeout=30.0
            )
            print(f"Screenshot saved to {output_file}")

        finally:
            # Clean up: close the server
            logging.debug("Closing proxy server...")
            server.close()
            await server.wait_closed()
            logging.debug("Proxy server closed")

    except asyncio.TimeoutError:
        print("Error: Screenshot capture timed out after 30 seconds")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
