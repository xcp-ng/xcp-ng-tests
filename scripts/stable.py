#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from textwrap import dedent, indent

from typing import NamedTuple, TextIO

# Add root project directory into PYTHONPATH
sys.path.append(str(Path(__file__).absolute().parent.parent))

# flake8: noqa: E402 module level import not at top of file
from lib.logger import setup_colored_logging

CHERRY_PICK = re.compile(r"cherry picked from commit ([0-9a-f]{40})")
GREEN, YELLOW = 32, 33  # ANSI colour codes

def colour(code: int, text: str, stream: TextIO = sys.stdout) -> str:
    """Colour only when a human is watching, and never when NO_COLOR asks us not to."""
    if os.environ.get("NO_COLOR") or not stream.isatty() or os.environ.get("TERM") == "dumb":
        return text
    return f"\033[{code}m{text}\033[0m"

# The git commands the release needs, nothing more. None reads or writes the index or
# the working tree, so the checkout it runs in can be dirty and on any branch.
def git(*args: str, stdin: str | None = None) -> str:
    """Run a git command and return its output, refusing to continue if it fails."""
    logging.debug(f"git {' '.join(args)}")
    result = subprocess.run(["git", *args], input=stdin, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()

def is_ancestor(commit: str, descendant: str) -> bool:
    try:
        git("merge-base", "--is-ancestor", commit, descendant)
    except RuntimeError:
        return False
    return True

def push(refspec: str, dry_run: bool) -> None:
    if dry_run:
        logging.debug(f"would run: git push origin {refspec}")
    else:
        git("push", "origin", refspec)

def branches() -> list[str]:
    """Return the commits master, testing and stable point at on the remote, never local ones."""
    return [git("rev-parse", f"refs/remotes/origin/{name}") for name in ("master", "testing", "stable")]

def short(commit: str) -> str:
    return git("rev-parse", "--short", commit)

def count(commits: str) -> int:
    return int(git("rev-list", "--count", commits))

def list_commits(commits: str) -> str:
    return "\n".join(f"  {line}" for line in git("log", "--format=%h  %s", commits).splitlines())

class Hotfix(NamedTuple):
    """A commit stable carries that master does not have, and whether it is already on master."""
    commit: str
    short: str
    subject: str
    decided: bool

def find_boundary(stable: str) -> str | None:
    """Return the merge that last released into stable, if there is one."""
    return git("rev-list", "--first-parent", "--merges", "-1", "--grep=^Released testing commit: ", stable) or None

def find_hotfixes(master: str, stable: str, boundary: str | None) -> list[Hotfix]:
    """Return the commits stable carries that master does not have, newest first.

    Three cases count as ported: an equivalent patch on master, a cherry-pick out of
    master into stable, and a cherry-pick out of the hotfix into master. A fix that
    reached master as a rewrite matches none of them, and only a person can say.
    """
    equivalent = {line.split()[1] for line in git("cherry", master, stable).splitlines() if line.startswith("- ")}
    # git cherry-pick -x writes "(cherry picked from commit <sha>)", which survives a
    # cherry-pick that had to be adjusted to apply, unlike the patch git cherry compares.
    ported = set(CHERRY_PICK.findall(git("log", "--format=%B", master)))

    args = ["log", "--no-merges", "--format=%H%x00%h%x00%s%x00%B%x01", f"{master}..{stable}"]
    if boundary:
        args += ["--not", boundary]
    hotfixes = []
    for record in git(*args).split("\x01"):
        if not record.strip():
            continue
        commit, short, subject, body = record.lstrip("\n").split("\x00", 3)
        from_master = any(is_ancestor(source, master) for source in CHERRY_PICK.findall(body))
        hotfixes.append(Hotfix(commit, short, subject, commit in equivalent or from_master or commit in ported))
    return hotfixes

def plural(count: int, singular: str, many: str | None = None) -> str:
    """Return "1 commit" or "2 commits", so summaries do not read like a form letter."""
    return f"{count} {singular if count == 1 else many or singular + 's'}"

def table(pairs: list[tuple[str, str]]) -> str:
    """Lay out label and value pairs in two aligned columns, for a terminal."""
    width = max(len(label) for label, _ in pairs)
    return "\n".join(f"  {label.ljust(width)}  {value}" for label, value in pairs)

def print_hotfixes(hotfixes: list[Hotfix]) -> None:
    """Describe the hotfixes that stable carries and master does not."""
    if not hotfixes:
        print(dedent("""\
            Hotfixes stable carries that master does not have

              None."""))
        return
    print(dedent("""\
        Hotfixes stable carries that master does not have

          A release replaces the content of stable with the content of the testing commit,
          so each of these has to be implemented in master already, or accepted as lost.
        """))
    for hotfix in hotfixes:
        print(f"   {colour(GREEN, '✓') if hotfix.decided else colour(YELLOW, '?')}  {hotfix.short}  {hotfix.subject}")
    if undecided := sum(not h.decided for h in hotfixes):
        print(f"\n  {plural(undecided, 'hotfix', 'hotfixes')} to decide on before the next release.")

def confirm(question: str, preset: bool) -> bool:
    """Ask a yes or no question, unless the answer was given on the command line."""
    if preset:
        return True
    if not sys.stdin.isatty():
        raise RuntimeError(dedent("""\
            This is not running in a terminal, so the questions cannot be asked.
            Pass -y to answer them, or run it from a terminal."""))
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")

def commit_message(testing: str, test_run: str | None, hotfixes: list[Hotfix]) -> str:
    """Build the message of the release merge, which is the only record of the release."""
    message = dedent(f"""\
        Merge testing into stable

        Released testing commit: {testing}
        """)
    if test_run:
        message += f"\nTest run: {test_run}\n"
    if hotfixes:
        message += "\nHotfixes in stable that this release discards, accounted for by the maintainer:\n\n"
        message += "".join(f"- {h.commit} {h.subject}\n" for h in hotfixes)
    return message

def prepare_cmd(args: argparse.Namespace) -> None:
    """Move testing to the tip of master, as a fast-forward."""
    master, testing, stable = branches()
    hotfixes = find_hotfixes(master, stable, find_boundary(stable))

    def report(after: str) -> None:
        print("Snapshot prepared\n")
        print(table([("master", short(master)), ("testing before", short(testing)), ("testing after", after)]))

    if not is_ancestor(testing, master):
        report("not moved")
        print("\n  testing holds commits that master does not have:\n")
        print(list_commits(f"{master}..{testing}"))
        print(indent(dedent("""
            Moving testing there now would need a force push and would drop them.
            Decide what to do with them, then run this again."""), "  "))
        raise RuntimeError("testing holds commits master does not have, not moving it.")

    if testing == master:
        # There is no snapshot to describe, so do not print one claiming nothing changed.
        report("unchanged")
        print(indent(dedent("""
            testing is already at the tip of master. Nothing was pushed and no test run
            was started: start one by hand against testing."""), "  "))
    else:
        push(f"{master}:refs/heads/testing", args.dry_run)
        report(f"{short(master)}  (not pushed)" if args.dry_run else short(master))
        gained = count(f"{testing}..{master}")
        change = git("diff", "--shortstat", testing, master)
        print(dedent(f"""
            The snapshot gained {plural(gained, 'commit')}.

            {change or 'The new snapshot has the same content as the old one.'}"""))
    print()
    print_hotfixes(hotfixes)

    # Give the command to run next, ready to paste, since that is easy to forget. A dry
    # run must not point at release: testing never moved, so nothing has been tested yet.
    if args.dry_run:
        print(dedent("""
            Next

              Nothing was moved, so there is nothing to test yet. Run this again without
              --dry-run to move testing, then start the test suite against it."""))
    else:
        print(dedent(f"""
            Next

              1. Start the test suite against testing, and wait for it to be green.
              2. Then release what it tested:

                   {sys.argv[0]} release --test-run <url>

              Do not run prepare again while a run is in flight: it would move testing under
              the suite and invalidate it."""))

def release_cmd(args: argparse.Namespace) -> None:
    """Release the tip of testing into stable, once the maintainer says it is safe."""
    master, testing, stable = branches()

    if is_ancestor(testing, stable):
        print(f"{short(testing)} is already in stable. Nothing to do.")
        return

    boundary = find_boundary(stable)
    hotfixes = find_hotfixes(master, stable, boundary)
    previous = (f"{short(boundary)}  {git('show', '-s', '--format=%s', boundary)}" if boundary
                else "none found, comparing against the whole of master")
    print("Release preflight\n")
    print(table([
        ("stable", short(stable)),
        ("master", short(master)),
        ("testing to release", short(testing)),
        ("previous release merge", previous),
    ]) + "\n")
    if extra := count(f"{master}..{testing}"):
        print(dedent(f"""\
            WARNING: the testing commit holds {plural(extra, 'commit')} that master does not have.
            They reached testing without going through master, so they are not on the default
            branch yet. Check that this is intended before releasing:
            """))
        print(list_commits(f"{master}..{testing}") + "\n")
    brought = count(f"{stable}..{testing}")
    change = git("diff", "--shortstat", f"{stable}^{{tree}}", f"{testing}^{{tree}}")
    print(dedent(f"""\
        What this release brings in

          {plural(brought, 'commit')} that stable does not have yet.
          Content change, hotfixes included: {change or 'no file changes'}
        """))
    # Only GitHub has a compare page at this address.
    if github := re.search(r"github\.com[:/](.+?)(?:\.git)?/?$", git("remote", "get-url", "origin")):
        print(f"  https://github.com/{github[1]}/compare/{stable}...{testing}\n")
    print_hotfixes(hotfixes)
    # Asked along with the questions when there is someone to ask, noted otherwise.
    ask_test_run = not args.test_run and not args.dry_run and not args.yes and sys.stdin.isatty()
    if not args.test_run and not ask_test_run:
        print("\nNo --test-run given, so the merge commit will not record where the run is reported.")
    print()

    # No need to check that stable did not move meanwhile: the merge has it as a
    # parent, so the push is refused if it is no longer a fast-forward.
    if not args.dry_run:
        if hotfixes and not confirm("Is every hotfix listed above implemented in master?", args.yes):
            raise RuntimeError("Not releasing, the hotfixes are not accounted for. Nothing was pushed.")
        if not confirm(f"Did the full test suite pass against {testing}?", args.yes):
            raise RuntimeError("Not releasing, the test run is not green. Nothing was pushed.")
        if ask_test_run:
            args.test_run = input("Where is the test run reported? (empty to leave it out) ").strip() or None

    # Built even in a dry run: commit-tree writes a dangling object and moves no ref, so
    # it is free, and it lets a dry run show the exact commit that would land.
    message = commit_message(testing, args.test_run, hotfixes)
    tree = git("rev-parse", f"{testing}^{{tree}}")
    commit = git("commit-tree", tree, "-p", stable, "-p", testing, "-F", "-", stdin=message)
    push(f"{commit}:refs/heads/stable", args.dry_run)

    merge = f"{short(commit)} to stable, as a merge of {short(stable)} and {short(testing)}."
    if args.dry_run:
        print(dedent(f"""\
            Dry run. Would have pushed {merge}
            Re-run without --dry-run to release."""))
    else:
        print(dedent(f"""\
            Pushed {merge}
              {commit}
            Run 'git log --first-parent stable' to see the release history."""))

def main() -> int:
    """Parse the command line and run the requested subcommand."""
    parser = argparse.ArgumentParser(description="Move testing to the tip of master, and release testing into stable.")
    # Options both subcommands share, so they can be given after the subcommand.
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--dry-run", action="store_true", help="Report what would happen, and change nothing")
    shared.add_argument("--debug", action="store_true", help="Log every git command, on stderr")

    subparsers = parser.add_subparsers(required=True, dest="command", metavar="COMMAND")
    prepare = subparsers.add_parser("prepare", parents=[shared], help="Move testing to the tip of master",
                                    description="Move testing to the tip of master. Does not start the test suite.")
    prepare.set_defaults(handler=prepare_cmd)
    release = subparsers.add_parser("release", parents=[shared], help="Release testing into stable",
                                    description="Release the tip of testing into stable, once the test suite is green.")
    release.add_argument("-y", "--yes", action="store_true",
                         help="Answer yes to every question, for non-interactive use")
    release.add_argument("--test-run", metavar="URL",
                         help="Where the test run is reported, recorded in the merge commit")
    release.set_defaults(handler=release_cmd)

    args = parser.parse_args()
    setup_colored_logging(logging.DEBUG if args.debug else logging.INFO)
    try:
        git("fetch", "origin")
        args.handler(args)
    except RuntimeError as error:
        # stdout is block buffered when it is piped or redirected, so flush it or the
        # error lands before the report it belongs after.
        sys.stdout.flush()
        logging.error(error)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
