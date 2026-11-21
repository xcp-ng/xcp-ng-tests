from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from lib.host import Host

from typing import Any

SCRIPT = os.path.join(os.path.dirname(__file__), 'scripts', 'kernel_checks.py')

def _result(host: Host, what: str, output: str) -> list[Any]:
    lines = output.strip().splitlines()
    assert lines, f"{what}: no output, the checker probably died (kernel oops?)"
    result = json.loads(lines[-1])
    for info in result['info']:
        logging.info(f"[{host}] {what}: {info}")
    assert not result['failures'], f"{what}: " + '; '.join(result['failures'])
    return result['info']

@dataclass
class KernelChecks:
    """Runs subcommands of scripts/kernel_checks.py, copied in `workdir` on `host`."""

    host: Host
    workdir: str

    def _cmd(self, args: tuple[str | int | float, ...], netns: str | None, timeout: int) -> str:
        prefix = f'ip netns exec {netns} ' if netns else ''
        return f"{prefix}timeout {timeout} python3 kernel_checks.py {' '.join(str(a) for a in args)}"

    def run(self, *args: str | int | float, netns: str | None = None, timeout: int = 600) -> list[Any]:
        """Run a subcommand, fail on reported failures, and return its "info" list."""
        out = self.host.ssh(f'cd {self.workdir} && {self._cmd(args, netns, timeout)}')
        return _result(self.host, str(args[0]), out)

    def start(self, name: str, *args: str | int | float, netns: str | None = None, timeout: int = 600) -> None:
        """Start a subcommand in the background. Get its result with `wait(name)`."""
        self.host.ssh(f'rm -f {self.workdir}/{name}.out {self.workdir}/{name}.done')
        self.host.ssh(f'cd {self.workdir} && ({self._cmd(args, netns, timeout)} > {name}.out 2>&1; '
                      f'touch {name}.done)', background=True)

    def wait(self, name: str, timeout: int = 120) -> list[Any]:
        self.host.ssh(f'cd {self.workdir} && '
                      f'timeout {timeout} sh -c "while [ ! -e {name}.done ]; do sleep 0.5; done"')
        return _result(self.host, name, self.host.ssh(f'cat {self.workdir}/{name}.out'))

def netns_cleanup(host: Host, *names: str) -> None:
    for name in names:
        host.ssh(f'ip netns del {name}', check=False)
