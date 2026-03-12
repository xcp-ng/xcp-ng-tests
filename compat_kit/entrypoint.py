#!/usr/bin/env python3
"""XCP-ng Compatibility Test Kit Entrypoint.

This script orchestrates the entire compatibility testing workflow:
1. Collect user inputs (host addresses, password)
2. Generate SSH keys and register them with ssh-agent (including compat_kit key if present)
3. Install SSH public keys on remote hosts
4. Join second host to pool (if provided)
5. Download VM image
6. Run test phases
7. Eject second host from pool and clean up SSH keys
"""

from __future__ import annotations

import argparse
import atexit
import getpass
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# flake8: noqa: E402
sys.path.append(f'{os.path.abspath(os.path.dirname(__file__))}/..')
from lib.commands import local_cmd
from lib.host import Host
from lib.pool import Pool

from typing import Optional

class ColoredFormatter(logging.Formatter):
    """A logging formatter that adds ANSI color codes to log levels."""

    # ANSI color codes matching pytest's colors
    COLORS = {
        logging.CRITICAL: "\033[91m",  # Red
        logging.ERROR: "\033[91m",     # Red
        logging.WARNING: "\033[93m",   # Yellow
        logging.INFO: "\033[92m",      # Green
        logging.DEBUG: "\033[95m",     # Magenta/Purple
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        original_levelname = record.levelname
        if color:
            record.levelname = f'{color}{record.levelname}{self.RESET}'
        result = super().format(record)
        record.levelname = original_levelname
        return result


def setup_colored_logging(level: int = logging.INFO) -> None:
    """Set up logging with colored formatter."""
    logger = logging.getLogger()
    logger.setLevel(level)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    handler = logging.StreamHandler()
    formatter = ColoredFormatter(
        fmt="%(asctime)s.%(msecs)03d %(levelname)s %(message)s", datefmt="%b %d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class State:
    """Global state for the compatibility test kit workflow."""

    def __init__(self) -> None:
        """Initialize state with default values."""
        self.temp_dir: Path | None = None
        self.skip_cleanup: bool = False
        self.master_host: str | None = None
        self.second_host: str | None = None
        self.log_dir: str | None = None
        self.vm_image_url: str | None = None
        self.dry_run: bool = False
        self.log_level: int = logging.INFO
        self.pool: Pool | None = None
        self.tests_failed: bool = False
        self.ssh_agent_pid: int | None = None
        self.ssh_key_path: Path | None = None
        self.second_host_joined_pool: bool = False
        self.password: str | None = None


# Global state instance
state = State()


def start_ssh_agent() -> None:
    """Start ssh-agent and set environment variables in os.environ.
    """
    if state.dry_run:
        logging.info("[DRY RUN] Would check/start SSH agent")
        return

    if 'SSH_AUTH_SOCK' in os.environ and os.environ['SSH_AUTH_SOCK']:
        logging.info("SSH agent already running, using existing agent")
        return

    logging.info("Starting new SSH agent")
    result = local_cmd(['ssh-agent', '-s'])
    # Parse the output to extract environment variables
    # Output looks like:
    # SSH_AUTH_SOCK=/tmp/ssh-XXXXXXptBxQL/agent.2; export SSH_AUTH_SOCK;
    # SSH_AGENT_PID=3; export SSH_AGENT_PID;
    # echo Agent pid 3;
    for line in result.stdout.split('\n'):
        if 'SSH_AUTH_SOCK=' in line:
            sock_value = line.split('=')[1].split(';')[0]
            os.environ['SSH_AUTH_SOCK'] = sock_value
        elif 'SSH_AGENT_PID=' in line:
            pid_value = line.split('=')[1].split(';')[0]
            state.ssh_agent_pid = int(pid_value)
            os.environ['SSH_AGENT_PID'] = pid_value


def cleanup() -> None:
    """Clean up resources on exit."""
    if not state.skip_cleanup:
        # Eject second host from pool if we joined it
        if state.second_host_joined_pool and state.pool and not state.dry_run:
            try:
                second_host = state.pool.hosts[-1]  # Second host is the last one added
                logging.info(f"Ejecting {second_host} from pool")
                state.pool.eject_host(second_host)
            except Exception as e:
                logging.warning(f"Failed to eject host from pool: {e}")

        # Remove key from agent (always try, regardless of who launched it)
        if not state.dry_run and state.ssh_key_path:
            try:
                local_cmd(['ssh-add', '-d', str(state.ssh_key_path)])
            except Exception as e:
                logging.warning(f"Failed to remove key from SSH agent: {e}")

        # Kill the agent only if we launched it
        if state.ssh_agent_pid is not None:
            logging.info("Terminating SSH agent we started")
            try:
                os.kill(state.ssh_agent_pid, 15)  # SIGTERM
            except ProcessLookupError:
                logging.debug("SSH agent already terminated")
            except Exception as e:
                logging.warning(f"Failed to kill SSH agent: {e}")

        if state.pool:
            logging.info("Removing SSH keys from hosts")
            for host in state.pool.hosts:
                try:
                    remove_ssh_key(host)
                except Exception as e:
                    logging.warning(f"Failed to remove key from {host}: {e}")

    # Always clean up temp directory, even if skip_cleanup is True
    if state.temp_dir and state.temp_dir.exists():
        try:
            shutil.rmtree(state.temp_dir)
        except Exception as e:
            logging.warning(f"Failed to remove temporary directory {state.temp_dir}: {e}")



def configure_ssh_keys() -> None:
    """Generate an ed25519 SSH key pair in the temp directory.
    
    Also registers the compat_kit key (if it exists) in the SSH agent.
    The generated key is for host access, the compat_kit key is for VM access.
    """
    logging.info("Generating SSH key pair")
    if state.dry_run:
        logging.info("[DRY RUN] Would generate SSH key in temp directory")
        return

    assert state.temp_dir is not None
    key_path = state.temp_dir / 'id_xcp_ng_compat_kit'
    compat_kit_key_path = Path(__file__).parent / 'id_ed25519'

    # Always generate a new key for host access
    local_cmd(['ssh-keygen', '-q', '-t', 'ed25519', '-f', str(key_path), '-N', '', '-C', 'xcp-ng-compat-kit'])
    key_path.chmod(0o600)
    state.ssh_key_path = key_path

    # Register the generated key with the SSH agent
    logging.info("Registering generated key with SSH agent")
    local_cmd(['ssh-add', str(key_path)])

    # Register the compat_kit key if it exists (used for VM access)
    if compat_kit_key_path.exists():
        logging.info(f"Registering compat_kit SSH key in agent: {compat_kit_key_path}")
        local_cmd(['ssh-add', str(compat_kit_key_path)])


def install_ssh_key(host: str, password: str) -> None:
    """Install the public key on a host via password authentication."""
    logging.info(f"Installing public key on {host}")
    if state.dry_run:
        logging.info(f"[DRY RUN] Would install SSH key on {host}")
        return

    assert state.ssh_key_path is not None
    pub_key_path = state.ssh_key_path.with_suffix(state.ssh_key_path.suffix + '.pub')

    # First, ensure .ssh directory exists on the remote host
    local_cmd([
        'sshpass',
        '-p', password,
        'ssh',
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        f'root@{host}',
        'mkdir -p ~/.ssh && chmod 700 ~/.ssh',
    ])

    # Read the public key from temp directory
    with open(pub_key_path) as f:
        public_key = f.read().strip()

    # Append the key to authorized_keys using sshpass
    # This avoids the interactive prompts that ssh-copy-id causes
    local_cmd([
        'sshpass',
        '-p', password,
        'ssh',
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        f'root@{host}',
        f"echo '{public_key}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys",
    ])


def remove_ssh_key(host: Host) -> None:
    """Remove the public key from a host's authorized_keys."""
    # Use the key comment (xcp-ng-compat-kit) to remove the key
    # This is more robust than trying to match the entire key
    if state.dry_run:
        logging.info(f"[DRY RUN] Would remove SSH key from {host}")
        return
    host.ssh("sed -i '/xcp-ng-compat-kit/d' /root/.ssh/authorized_keys")


def validate_pool_constraints(pool: Pool, second_host: str | None) -> None:
    """Validate that the pool contains only the expected hosts.
    
    Ensures the pool will contain exactly the hosts specified by the user.
    If a second_host is provided, the pool must be either:
    - A 1-host pool (only the master) - will join second_host later
    - A 2-host pool already containing both master and second_host
    
    If no second_host is provided, the master's pool must contain only 1 host.
    
    Args:
        pool: The master host's pool
        second_host: The second host hostname_or_ip (optional)
        
    Raises:
        ValueError: If pool constraint is violated
    """
    num_hosts = len(pool.hosts)

    if second_host is None:
        if num_hosts != 1:
            raise ValueError(
                f"Pool constraint violated: Master host's pool contains {num_hosts} host(s), "
                f"but only 1 host was provided. The pool must contain only the specified hosts. "
                f"Please eject the extra hosts before running the compatibility kit."
            )
    else:  # second_host is provided
        second_in_pool = any(h.hostname_or_ip == second_host for h in pool.hosts)

        if second_in_pool:
            # Second host already in pool - verify it's a 2-host pool
            if num_hosts != 2:
                raise ValueError(
                    f"Pool constraint violated: Second host is already in master's pool, "
                    f"but the pool contains {num_hosts} host(s). Only 2 hosts are expected "
                    f"(master and second). Please eject the extra hosts before running the compatibility kit."
                )
        else:
            # Second host not in pool - master must be alone
            if num_hosts != 1:
                raise ValueError(
                    f"Pool constraint violated: Master host's pool contains {num_hosts} host(s), "
                    f"but only the master host should be present before joining the second host. "
                    f"Please eject the extra hosts before running the compatibility kit."
                )


def join_second_host_to_pool() -> None:
    """Join the second host to the pool if provided and not already in the pool.
    
    Creates a temporary Pool object for the second host (in its own pool) and then
    calls join_pool() to join it to the master's pool. Sets state.second_host_joined_pool
    so cleanup knows to eject it later.
    
    Assumes both hosts have the same SSH root password, which is obtained from
    state.pool.master.password.
    
    Skipped if:
    - No second host is configured
    - Pool is not constructed
    - Second host is already in the pool
    - Running in dry-run mode
    """
    if not state.second_host or not state.pool:
        return

    if state.dry_run:
        logging.info("[DRY RUN] Would join second host to pool")
        return

    # Check if second host is already in the pool by checking hostname_or_ip
    if any(h.hostname_or_ip == state.second_host for h in state.pool.hosts):
        logging.info(f"Host {state.second_host} is already in the pool")
        return

    logging.info(f"Joining {state.second_host} to the pool")
    # Create a temporary Pool object for the second host to call join_pool
    # The host will be in its own pool initially
    second_host_pool = Pool(state.second_host)
    second_host = second_host_pool.master
    # Override the password to use the master's password (assumes both hosts have same password)
    second_host.password = state.pool.master.password
    second_host.join_pool(state.pool)
    state.second_host_joined_pool = True
    logging.info(f"Successfully joined {state.second_host} to the pool")


def run_pytest(phase: int, test_args: list[str], log_file: str) -> None:
    """Run pytest with the given arguments.

    Args:
        phase: Phase number (for display)
        test_args: List of pytest arguments
        log_file: Filename for the log file
    """
    assert state.log_dir is not None
    log_path = Path(state.log_dir) / log_file

    logging.info(f"Phase {phase}: Running tests")

    cmd = [
        'pytest',
        '--color=yes',
        '--no-header',
        '--maxfail=0',
        '--log-file-level=debug',
        f'--log-file={log_path}',
        f'--log-cli-level={logging.getLevelName(state.log_level)}',
        f'--vm={state.vm_image_url}',
    ] + test_args

    if state.dry_run:
        cmd += ['--collect-only', '-q']
    elif state.pool is not None:
        cmd += [f'--hosts={state.pool.master.hostname_or_ip}']

    logging.debug(f"Running: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, timeout=3600)
        logging.info(f"Phase {phase}: Tests completed successfully")
    except subprocess.CalledProcessError as e:
        logging.warning(f"Phase {phase}: Tests failed with exit code {e.returncode}")
        state.tests_failed = True
        # Don't raise, allow other phases to run
    except subprocess.TimeoutExpired:
        logging.error(f"Phase {phase}: Tests timed out")
        state.tests_failed = True
        raise


def setup_config_files() -> None:
    """Copy -dist files to their actual locations and set the password.
    
    Copies data.py-dist and vm_data.py-dist to their actual locations,
    then updates HOST_DEFAULT_PASSWORD in data.py with the user-provided password.
    Password is properly escaped to handle special characters in string literals.
    """
    logging.info("Setting up configuration files")
    if state.dry_run:
        logging.info("[DRY RUN] Would copy data.py-dist to data.py and vm_data.py-dist to vm_data.py")
        return

    base_path = Path(__file__).parent.parent

    data_dist = base_path / 'data.py-dist'
    data_py = base_path / 'data.py'
    logging.debug(f"Copying {data_dist} to {data_py}")
    shutil.copy2(data_dist, data_py)

    # Replace the password placeholder in data.py
    if state.password:
        logging.debug(f"Setting HOST_DEFAULT_PASSWORD in {data_py}")
        with open(data_py, 'r') as f:
            content = f.read()
        # Escape the password for use in a string literal
        escaped_password = state.password.replace('\\', '\\\\').replace('"', '\\"')
        # Replace the empty password with the provided password
        content = content.replace('HOST_DEFAULT_PASSWORD = ""', f'HOST_DEFAULT_PASSWORD = "{escaped_password}"')
        with open(data_py, 'w') as f:
            f.write(content)

    vm_data_dist = base_path / 'vm_data.py-dist'
    vm_data_py = base_path / 'vm_data.py'
    logging.debug(f"Copying {vm_data_dist} to {vm_data_py}")
    shutil.copy2(vm_data_dist, vm_data_py)


def run_phase_1() -> None:
    """Run single-host tests (Phase 1)."""
    test_args = [
        'tests/misc/test_basic_without_ssh.py',
        'tests/misc/test_export.py',
        'tests/misc/test_vm_basic_operations.py',
        'tests/system/test_systemd.py',
        'tests/xapi_plugins/plugin_lsblk',
        'tests/pci_passthrough/test_pci_passthrough.py',
        '-m',
        'not hostA2 and not hostB1',
    ]
    run_pytest(1, test_args, "test_kit_1.log")


def run_phase_2() -> None:
    """Run two-host pool tests (Phase 2)."""
    if state.second_host is None:
        logging.info("Phase 2: Skipped (no second host provided)")
        return

    test_args = [
        'tests/storage/ext/test_ext_sr_intrapool_migration.py',
        '-m', 'hostA2 and not hostB1',
    ]
    run_pytest(2, test_args, "test_kit_2.log")


def set_hvm_fep(enabled: bool) -> None:
    """Set or unset the hvm_fep Xen parameter.

    Args:
        enabled: If True, enable hvm_fep; if False, disable it
    """
    if state.dry_run:
        logging.info(f"[DRY RUN] Would {'enable' if enabled else 'disable'} hvm_fep")
        return

    if enabled:
        logging.info("Enabling hvm_fep")
        cmd = '/opt/xensource/libexec/xen-cmdline --set-xen hvm_fep=1'
    else:
        logging.info("Disabling hvm_fep")
        cmd = '/opt/xensource/libexec/xen-cmdline --delete-xen hvm_fep'

    assert state.pool is not None
    state.pool.master.ssh(cmd)


def run_phase_3() -> None:
    """Run Xen-level tests (Phase 3) with hvm_fep enabled."""
    try:
        set_hvm_fep(True)
        if not state.dry_run:
            assert state.pool is not None
            state.pool.master.reboot(verify=True)

        test_args = [
            'tests/xen',
        ]
        run_pytest(3, test_args, "test_kit_3.log")
    finally:
        try:
            set_hvm_fep(False)
            if not state.dry_run:
                assert state.pool is not None
                state.pool.master.reboot(verify=True)
        except Exception as e:
            logging.warning(f"Failed to disable hvm_fep and reboot: {e}")


def print_summary() -> None:
    """Print a colored summary of the test results."""
    # ANSI color codes
    RED = '\033[91m'
    GREEN = '\033[92m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

    assert state.log_dir is not None
    logging.info("=" * 80)
    if state.dry_run:
        logging.info(f"{BOLD}{GREEN}✅ Dry-run completed successfully{RESET}")
        logging.info("")
        logging.info(f"{GREEN}Preview of what would be executed completed. To run the actual compatibility{RESET}")
        logging.info(f"{GREEN}tests, execute the command without the --dry-run flag.{RESET}")
    elif state.tests_failed:
        logging.error(f"{BOLD}{RED}❌ Compatibility test kit FAILED{RESET}")
        logging.error("")
        logging.error(f"{RED}Some tests did not pass. Please review the log files for details:{RESET}")
        log_dir_path = Path(state.log_dir)
        for log_file in sorted(log_dir_path.glob("test_kit_*.log")):
            logging.error(f"{RED}  - {log_file.name}{RESET}")
        logging.error("")
        logging.error(f"{RED}Please send the following files to XCP-ng developers for review:{RESET}")
        logging.error(f"{RED}  - Log files{RESET}")
        logging.error(f"{RED}  - Hardware details and system information{RESET}")
    else:
        logging.info(f"{BOLD}{GREEN}✅ Compatibility test kit PASSED{RESET}")
        logging.info("")
        logging.info(f"{GREEN}Your hardware has successfully passed the XCP-ng compatibility test kit!{RESET}")
    logging.info("=" * 80)


def run_workflow(
    master_host: str,
    second_host: Optional[str],
    password: str,
    log_dir: str,
    skip_cleanup: bool,
    vm_image_url: str,
    dry_run: bool = False,
) -> None:
    """Execute the full compatibility test kit workflow.
    Args:
        master_host: IP or hostname of the pool master
        second_host: IP or hostname of the second host (optional)
        password: SSH root password
        log_dir: Directory where log files will be written
        skip_cleanup: If True, don't remove SSH keys on exit
        vm_image_url: URL to the VM image to download
        dry_run: If True, only print what would be done without executing
    """
    # Register cleanup handler early, before any state initialization
    atexit.register(cleanup)

    try:
        # Initialize state
        temp_dir = tempfile.mkdtemp(prefix='xcp-ng-compat-')
        state.temp_dir = Path(temp_dir)
        state.master_host = master_host
        state.second_host = second_host
        state.password = password
        state.log_dir = log_dir
        state.skip_cleanup = skip_cleanup
        state.vm_image_url = vm_image_url
        state.dry_run = dry_run
        logging.info("=" * 80)
        title = "XCP-ng Compatibility Test Kit"
        logging.info(" " * ((80 - len(title))//2) + title)
        logging.info("=" * 80)
        if dry_run:
            logging.info("[DRY RUN] No actual changes will be made")
        start_ssh_agent()
        setup_config_files()
        configure_ssh_keys()
        install_ssh_key(master_host, password)
        if second_host is not None:
            install_ssh_key(second_host, password)

        # Construct Pool after SSH key installation
        if not dry_run:
            state.pool = Pool(master_host)
            validate_pool_constraints(state.pool, second_host)
            # Join second host to the pool if provided
            join_second_host_to_pool()
        else:
            # In dry-run mode, skip Pool construction
            logging.info("[DRY RUN] Would construct Pool()")

        run_phase_1()
        run_phase_2()
        run_phase_3()
        print_summary()
    except KeyboardInterrupt:
        logging.error("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


def main() -> None:
    """Parse arguments and run the compatibility test kit."""
    default_vm_url = "https://nextcloud.vates.tech/index.php/s/MmEjo8qYo7Ccs2B/download"
    parser = argparse.ArgumentParser(description="XCP-ng Compatibility Test Kit - Automated testing on new hardware")
    parser.add_argument('--master-host', help="IP or hostname of the pool master")
    parser.add_argument("--second-host", help="IP or hostname of the second host (optional, for pool tests)")
    parser.add_argument("--password", help="SSH root password (if not provided, will be prompted)")
    parser.add_argument("--log-dir", default="/app/logs",
                        help="Directory where log files will be written (default: /app/logs)",
    )
    parser.add_argument("--log-level", default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Logging level (default: INFO)")
    parser.add_argument("--vm-image-url", default=default_vm_url,
        help=f"URL to the VM image to download (default: {default_vm_url})")
    parser.add_argument("--skip-cleanup", action="store_true",
        help="Don't remove SSH keys from hosts on exit")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without making any changes")
    parser.add_argument("--non-interactive", action="store_true",
        help="Don't prompt for missing parameters; fail if required arguments are missing")

    args = parser.parse_args()

    log_level = getattr(logging, args.log_level.upper())
    setup_colored_logging(log_level)
    state.log_level = log_level


    # Collect missing parameters
    master_host = args.master_host
    if not master_host:
        if args.non_interactive:
            logging.error("Master host is required (use --master-host)")
            sys.exit(1)
        master_host = input("Enter the IP or hostname of the pool master: ").strip()
        if not master_host:
            logging.error("Master host is required")
            sys.exit(1)

    second_host = args.second_host
    if not second_host and not args.non_interactive:
        response = input("Enter the IP or hostname of the second host (or press Enter to skip): ").strip()
        second_host = response if response else None

    password = args.password
    if not password:
        if args.non_interactive:
            logging.error("Password is required (use --password)")
            sys.exit(1)
        password = getpass.getpass("Enter SSH root password: ")
        if not password:
            logging.error("Password is required")
            sys.exit(1)

    # Ensure log directory exists
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Run the compatibility test kit
    run_workflow(
        master_host=master_host,
        second_host=second_host,
        password=password,
        log_dir=str(log_dir),
        skip_cleanup=args.skip_cleanup,
        vm_image_url=args.vm_image_url,
        dry_run=args.dry_run,
    )

    # Exit with error code if tests failed
    if state.tests_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
