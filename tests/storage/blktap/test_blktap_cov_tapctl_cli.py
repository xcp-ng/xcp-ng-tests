from __future__ import annotations

import pytest

import re
import shlex

from tests.storage.blktap import blktap_tool

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.blktap import TapCtl
    from lib.host import Host

# The tap-ctl command line: help, error reporting and the commands not used by the other tests.
#
# Requirements:
# - an XCP-ng host with blktap

COMMANDS = ['list', 'allocate', 'free', 'create', 'destroy', 'spawn', 'attach', 'detach', 'open', 'close',
            'pause', 'unpause', 'stats', 'major', 'check', 'commit', 'query', 'cancel']

def tap_ctl(host: Host, *args: object) -> tuple[int, str]:
    """Run tap-ctl, return its exit code with its output (stderr included)."""
    result = host.ssh_with_result(shlex.join([blktap_tool(host, 'tap-ctl')] + [str(arg) for arg in args]))
    return result.returncode, result.stdout

@pytest.mark.no_vm
@pytest.mark.parametrize('command', COMMANDS)
def test_command_help(host: Host, command: str) -> None:
    _, output = tap_ctl(host, command, '-h')
    assert f'usage: {command}' in output, f"No usage for {command}: {output!r}"

@pytest.mark.no_vm
@pytest.mark.parametrize('arguments', [[], ['no-such-command']])
def test_help_lists_the_commands(host: Host, arguments: list[str]) -> None:
    _, output = tap_ctl(host, *arguments)
    assert output.startswith('usage: tap-ctl COMMAND'), f"No usage: {output!r}"
    for command in COMMANDS:
        assert command in output, f"{command} is not listed: {output!r}"

# (arguments, exit code): the exit code is the errno
FAILING_COMMANDS = [
    (['stats'], 22),  # missing -p and -m
    (['open', '-p', '1'], 22),
    (['create'], 22),
    (['spawn', '-x'], 22),
    (['free', '-m', '99999'], 22),
    (['stats', '-p', '999999', '-m', '0'], 2),  # no such tapdisk
    (['stats', '-p', 'abc', '-m', '0'], 2),
    (['destroy', '-p', '999999', '-m', '5'], 2),
    (['attach', '-p', '1', '-m', '99999'], 2),
    (['close', '-p', '999999', '-m', '1'], 2),
    (['pause', '-p', '999999', '-m', '1'], 2),
    (['commit', '-p', '999999', '-m', '1', '-a', '/nonexistent'], 2),
    (['query', '-p', '999999', '-m', '1'], 2),
    (['cancel', '-p', '999999', '-m', '1'], 2),
]

@pytest.mark.no_vm
@pytest.mark.parametrize('arguments, expected_code', FAILING_COMMANDS,
                         ids=[' '.join(arguments) for arguments, _ in FAILING_COMMANDS])
def test_failing_command(host: Host, arguments: list[str], expected_code: int) -> None:
    code, output = tap_ctl(host, *arguments)
    assert code == expected_code, f"Unexpected exit code {code}: {output!r}"
    # the sign of the code printed depends on the command
    assert re.search(rf'ERROR \[-?{expected_code} ', output), f"No error reported: {output!r}"

@pytest.mark.no_vm
def test_major_and_check(host: Host) -> None:
    code, output = tap_ctl(host, 'major')
    assert code == 0 and output.strip().isdigit(), f"Unexpected tap-ctl major output {output!r}"
    code, output = tap_ctl(host, 'check')
    assert code == 0 and output.strip() == 'ok', f"tap-ctl check failed: {output!r}"

@pytest.mark.no_vm
@pytest.mark.parametrize('image', ['vhd'], indirect=True)
def test_list_filters_and_destroy(host: Host, tapctl: TapCtl, image: str) -> None:
    """Each filter of list selects the tapdisk, a non-matching one selects nothing; destroy removes it."""
    pid, minor = tapctl.create(image)
    image_type, path = image.split(':', 1)

    for filters in (['-p', pid], ['-m', minor], ['-t', image_type], ['-f', path]):
        code, output = tap_ctl(host, 'list', *filters)
        assert code == 0 and f'pid={pid} ' in output, f"list {filters} doesn't show the tapdisk: {output!r}"

    for filters in (['-p', 999999], ['-m', 99999], ['-t', 'qcow2'], ['-f', '/nonexistent']):
        code, output = tap_ctl(host, 'list', *filters)
        assert code == 0 and f'pid={pid} ' not in output, f"list {filters} shows the tapdisk: {output!r}"

    stats = tapctl.stats(pid, minor)
    assert stats['name'] == image, f"Unexpected stats {stats}"

    tapctl.destroy(pid, minor)
    assert not tapctl.list(pid=pid), "The tapdisk is still listed after destroy"
