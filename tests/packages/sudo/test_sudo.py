from lib import commands
from lib.host import Host

# Functional tests for the sudo package: a sudoers.d drop-in rule must be
# enforced by sudo, both for the command it grants and for any command it
# doesn't.
#
# Requirements:
# - an XCP-ng host (--hosts)

def _attempt_sudo(host: Host, user: str, cmd: str) -> commands.SSHResult[str]:
    # -n: never prompt for a password, fail immediately instead of hanging
    # if the rule isn't honored.
    return host.ssh_with_result(f"su - {user} -c 'sudo -n {cmd}'")

def test_sudo_allowed_cmd(host: Host, whoami_sudoer: str) -> None:
    result = _attempt_sudo(host, whoami_sudoer, "/usr/bin/whoami")
    assert result.returncode == 0 and result.stdout.strip() == "root", (
        f"expected the granted command to run as root, got {result}"
    )

def test_sudo_denied_cmd(host: Host, whoami_sudoer: str) -> None:
    result = _attempt_sudo(host, whoami_sudoer, "/usr/bin/id")
    assert result.returncode != 0, "user was able to run a command not granted by the drop-in"
