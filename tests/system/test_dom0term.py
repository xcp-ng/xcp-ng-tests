import pytest

import logging

from lib.common import wait_for
from lib.host import Host
from lib.vm import VM

# Requirements:
# - an XCP-ng host (--hosts) >= 8.3, with the `vncterm` package installed.
# - for TestGuestVNCConsole: a Linux VM (--vm). A PV/PVH guest uses `vncterm`.
#   A HVM guest can't be used since their text console is served by QEMU and
#   the vncterm-specific assertions are skipped.
#
# What this covers:
#   `vncterm` has two jobs:
#     1. The dom0term.service exposes the dom0 text console over VNC bound to
#        127.0.0.1, driven by /opt/xensource/libexec/vncterm-wrapper.
#     2. xenopsd spawns one `vncterm` per Linux guest to turn the PV text
#        console into a VNC stream, recording pid/port in xenstore.
#   This module tests both cases.

VNCTERM_BIN = "/usr/lib64/xen/bin/vncterm"
VNCTERM_UNPRIV_USER = "vncterm_base"


def _rfb_greeting(host: Host, port: str) -> str:
    """Return the RFB protocol greeting from 127.0.0.1:<port> on the host."""
    return host.ssh(
        f"timeout 5 bash -c 'exec 3<>/dev/tcp/127.0.0.1/{int(port)}; head -c 12 <&3'"
    )


@pytest.mark.usefixtures("host_at_least_8_3")
class TestDom0Term:
    def test_vncterm_installed(self, host: Host) -> None:
        host.ssh("rpm -q vncterm")
        host.ssh(f"test -x {VNCTERM_BIN}")

    def test_dom0term_service_enabled_and_active(self, host: Host) -> None:
        assert host.ssh("systemctl is-enabled dom0term.service") == "enabled"
        assert host.ssh("systemctl is-active dom0term.service") == "active"

    def test_dom0term_runs_as_root(self, host: Host) -> None:
        out = host.ssh("ps -o user= -C vncterm")
        assert out, "no vncterm process found for dom0term"
        users = {line.strip() for line in out.splitlines()}
        assert users == {"root"}, f"unexpected dom0term vncterm user(s): {users}"

    @pytest.mark.parametrize("key", ["vnc-port", "tc-port"])
    def test_dom0term_ports_localhost_only(self, host: Host, key: str) -> None:
        # vnc-port is the VNC console, tc-port the raw text console (-T). So,
        # both connections give access to dom0's root login, so both MUST only
        # be available on loopback.
        port = host.ssh(f"xenstore-read /local/domain/0/console/{key}")
        assert port.isdigit()
        # Every TCP listener on that port, whatever address it is bound to
        # (wildcard as 0.0.0.0, [::] or *, a real IP, an IPv6 address with a
        # %iface scope...). The local address is the 4th column of `ss`.
        out = host.ssh(f"ss -ltnH 'sport = :{port}'")
        addrs = [line.split()[3].rsplit(":", 1)[0] for line in out.splitlines() if line.strip()]
        assert addrs, f"nothing listening on port {port} ({key})"
        non_loopback = [a for a in addrs
                        if not (a.split("%")[0].startswith("127.") or a.split("%")[0] == "[::1]")]
        assert not non_loopback, \
            f"dom0 console {key} {port} is bound to non-loopback address(es) {non_loopback}\n{out}"

    def test_dom0term_rfb_handshake(self, host: Host) -> None:
        port = host.ssh("xenstore-read /local/domain/0/console/vnc-port")
        greeting = _rfb_greeting(host, port)
        assert greeting.startswith("RFB 00"), f"unexpected RFB greeting: {greeting!r}"

    def test_dom0term_recovers_after_kill(self, host: Host) -> None:
        # Restart=always in the unit: killing vncterm must not leave dom0
        # without a console.
        old = host.ssh("xenstore-read /local/domain/0/vncterm-pid")
        host.ssh("pkill -x vncterm", check=False)
        host.ssh(
            "for i in $(seq 30); do "
            "p=$(xenstore-read /local/domain/0/vncterm-pid 2>/dev/null); "
            f'[ -n "$p" ] && [ "$p" != {old} ] && kill -0 "$p" 2>/dev/null && exit 0; '
            "sleep 1; done; exit 1"
        )
        port = host.ssh("xenstore-read /local/domain/0/console/vnc-port")
        assert _rfb_greeting(host, port).startswith("RFB 00")


@pytest.mark.small_vm
@pytest.mark.usefixtures("host_at_least_8_3")
class TestGuestVNCConsole:
    def test_guest_console_over_vnc(self, host: Host, imported_vm: VM) -> None:
        vm = imported_vm
        vm.start()
        try:
            vm.wait_for_vm_running_and_ssh_up()
            domid = vm.param_get("dom-id")

            pid = host.ssh(f"xenstore-read /local/domain/{domid}/vncterm-pid",
                           check=False).strip()
            if not pid:
                pytest.skip("no per-guest vncterm (HVM guest: console served by QEMU)")

            assert host.ssh(f"cat /proc/{pid}/comm").strip() == "vncterm"

            port = host.ssh(f"xenstore-read /local/domain/{domid}/console/vnc-port")
            assert port.isdigit()

            # The deprivileged worker is the forked child of the recorded pid.
            children = host.ssh(f"pgrep -P {pid} -x vncterm", check=False).split()
            assert len(children) == 1, \
                f"expected exactly one deprivileged vncterm child of {pid}, got {children}"
            child = children[0]
            base_uid, base_gid = host.ssh(
                f"getent passwd {VNCTERM_UNPRIV_USER} | cut -d: -f3,4"
            ).split(":")
            expected_uid = int(base_uid) + int(port)
            expected_gid = int(base_gid) + int(port)
            # /proc/<pid>/status: "Uid: real effective saved fs" (same for Gid);
            # all four must be dropped, so no way back to root.
            uids = host.ssh(f"awk '/^Uid:/{{print $2,$3,$4,$5}}' /proc/{child}/status").split()
            gids = host.ssh(f"awk '/^Gid:/{{print $2,$3,$4,$5}}' /proc/{child}/status").split()
            assert uids == [str(expected_uid)] * 4, \
                f"vncterm child {child} uids {uids}, expected {expected_uid}"
            assert gids == [str(expected_gid)] * 4, \
                f"vncterm child {child} gids {gids}, expected {expected_gid}"
            assert host.ssh(f"readlink /proc/{child}/root") == f"/var/xen/vncterm/{pid}", \
                f"vncterm child {child} is not chrooted into its scratch directory"
            greeting = _rfb_greeting(host, port)
            assert greeting.startswith("RFB 00"), f"unexpected RFB greeting: {greeting!r}"
            logging.info("guest %s: vncterm pid=%s port=%s greeting=%r",
                         vm.uuid, pid, port, greeting)
        finally:
            vm.shutdown(force=True, verify=True)

    def test_no_vncterm_leak_across_vm_lifecycle(self, host: Host, imported_vm: VM) -> None:
        vm = imported_vm
        before = host.ssh("pgrep -xc vncterm", check=False).strip() or "0"
        vm.start()
        vm.wait_for_vm_running_and_ssh_up()
        vm.shutdown(force=True, verify=True)
        after = host.ssh("pgrep -xc vncterm", check=False).strip() or "0"
        assert after == before, \
            f"vncterm process count changed across VM lifecycle: {before} -> {after}"
