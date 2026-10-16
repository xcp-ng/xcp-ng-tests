# Release process

`stable` is the branch people and CI run the tests from. A release is a merge
into `stable`. There is no tag or changelog: `git log --first-parent stable` is
the release history.
The release merge tree is exactly that of a tested `testing` commit, so any result on
`stable` maps to one `master` commit. 

- `master`: development, the default branch.
- `testing`: a snapshot of `master`, frozen while the full test suite (several hours)
  runs against it.
- `stable`: the last released `testing` content, plus any hotfixes since.

```
master   o---o---T1---o---o---h'---T2
                   \                 \
stable   --R0-------R1-------h--------R2
```

- `o`, `h`, `h'`: pull request merges. Their branches are not shown.
- `T1`, `T2`: tips of `master` that `prepare` pointed `testing` at, and that were tested.
- `R1`, `R2`: release merges. Their first parent is the previous `stable`, the second is
  the tested commit, and their tree is the tested commit's tree, unchanged. So the
  content of `h` is lost in `R2`: `R2` has the tree of `T2`, not the tree of `h`.
- `h`: a hotfix pull request merged into `stable`.
- `h'`: `h` ported to `master` with `git cherry-pick -x`. It is the only way the fix
  gets into `T2`, and so into `R2`.

## Updating stable

`./scripts/stable.py` can be used to ease the release process. It's used as follows:

1. `./scripts/stable.py prepare` fast-forwards `testing` to `master`. It refuses if
   `testing` has commits `master` lacks, and lists the hotfixes still to port.
2. Manually run the full test suite against `testing` until it is green. Do not run `prepare`
   meanwhile: it would move `testing` under the running suite.
3. `./scripts/stable.py release` shows what the release brings in and
   the pending hotfixes, asks for confirmation (`-y` outside a terminal), and pushes the
   merge, recording the released commit.

The merge is built with `git commit-tree` from the `testing` tree: it cannot conflict
and cannot change a file. If the run fails, `stable` was not touched: fix it on
`master` and start again at step 1.

## Hotfixes

When a product change breaks a test on `stable` (for example a newer Xen raising a
vCPU limit), fix it with a pull request straight into `stable`, merged with
**Create a merge commit**. Then port it to `master`, with `git cherry-pick -x` or a
proper fix. A release replaces the content of `stable`, so a hotfix that is not on
`master` is dropped.

Both subcommands list the hotfixes added since the last release:

- ✓ already on `master`: an equivalent patch, or a `cherry picked from` trailer in
  either direction.
- ? the script cannot tell (never ported, or rewritten): read the diff and decide.
