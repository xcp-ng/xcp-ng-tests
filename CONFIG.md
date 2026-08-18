# XCP-ng-tests configuration

This document explains the configuration design explored by the TOML
proof-of-concept (POC). It first summarizes the `data.py` approach in
`master`, because its limitations motivate the design. The rest of the
document explains how the TOML design is assembled, why it is structured this
way, and which parts of the test setup remain outside it.

## Existing approach: `data.py`

In `master`, each test environment has its own `data.py`, usually created by
copying `data.py-dist` and editing it. Tests import its constants directly.
`jobs.py` uses a `vm_data.py` that describes the VM test matrix, while
`tools.py` reads a separate inventory TOML file.

That approach makes the project configuration difficult to maintain:

- Defaults and environment-specific values are mixed in one private copy.
- Updating the defaults requires manually merging changes into each copy.
- Several worktrees require several copies of the same configuration.
- Loose module-level constants do not show how settings are grouped or used.
- Typos, wrong types, and missing values have no common schema to catch them.
- Runtime overrides need dedicated environment variables to have been added
  to `data.py` in advance.
- CI configurations duplicate almost all of their values even when only a few
  settings differ.
- Tests, tools, and jobs do not use one configuration and inventory model.
- Running a test directly requires passing its hosts on the command line.
- Type checking and test collection depend on a developer creating a local
  `data.py` first.
- Nothing can display the effective configuration or compare two environments.

## Why this design

## The new TOML design

This design moves configuration out of `data.py` and into TOML files. The project ships a default configuration
that can be updated over time, while each local setup can keep a smaller file containing only its own changes. It also
becomes possible to override individual values temporarily for one command or one test session without editing
configuration files. More specifically:

- A declarative markup language, TOML, is used to make validation easier and to avoid tying configuration to Python code
  checks.
- A `lib/config.toml` file containing all configuration entries is now shipped with the project, making it possible to
  update the default values globally over time.
- The user now defines a local overlay configuration, typically called `config.local.toml`, that contains only the
  values that differ from the default configuration.
- Short names can be used to select a specific overlay, making it easy to switch between different lab configurations
  (e.g. the short name `mylab` selects the `config.mylab.toml` overlay).
- Generic environment variables of the form `XCPNG_TESTS_*` can be used as temporary configuration-value overrides,
  avoiding the need to edit configuration files during a test session.
- A similar `--config-value` command-line option provides temporary overrides for specific configuration entries during
  a single command invocation.
- An `include` directive makes it possible to split shared configuration across different files, which helps when
  managing more complex configurations such as CI-related ones.
- After gathering configuration values from the different sources, a Pydantic model validates the result and provides
  tests and tools with one properly typed configuration object.
- Dedicated tooling is provided to display the effective configuration, export it, or compare it to another resolved
  configuration.

To ease the transition, a migration tool is also provided to automatically convert `data.py` into the corresponding
`config.local.toml` overlay.

The design does not attempt to move the VM test matrix or job definitions out
of Python yet.

## Building the effective configuration

The loader turns the separate sources into one validated configuration. The
configuration sources are applied in this order, from highest to lowest
precedence:

1. Command-specific overrides, such as pytest's `--volume-size`.
2. `--config-value KEY=VALUE` options.
3. `XCPNG_TESTS_*` environment variables.
4. The selected overlay and its includes, or the implicitly selected local
   overlay (`config.local.toml`) and its includes when no overlay is selected.
5. `lib/config.toml` and its includes.

An example of each configuration source:

```toml
# lib/config.toml: the shipped defaults and its includes
[host]
default_user = "root"
```

```toml
# config.cgt1.toml: a selected overlay and its includes
[host]
default_password = "secret"
```

```bash
# XCPNG_TESTS_* environment variable
XCPNG_TESTS_network__free_nics='["eth1", "eth2"]'
```

```bash
# --config-value option
pytest --config-value 'hosts."10.30.0.56".user=root'
```

```bash
# command-specific override
pytest --volume-size=10GiB
```

The selected overlay and the implicitly selected local overlay
(`config.local.toml`) are alternative sources. The local overlay is used only
when no overlay is selected. The first four configuration sources are handled
by the configuration loader. The final configuration source belongs to the
command consuming the configuration, so its details depend on whether the
command is `pytest`, `jobs.py`, or `tools.py`.

## The shipped default configuration

`lib/config.toml` is the base configuration for xcp-ng-tests. It contains the
default values used by the parts of the tests and `jobs.py` that consume
environment configuration, as well as the defaults reused by the separate
inventory TOML file consumed by `tools.py`. It is versioned and shipped with
the project, so a fresh checkout has a valid configuration model without
requiring a user-created Python module.

Environment-specific values belong in an overlay rather than in
`lib/config.toml`. The project can then update the shipped defaults without
overwriting local configuration or asking users to merge their changes into a
new copy of the file. An overlay records only its deviations, so it receives
new defaults automatically. If an environment must keep an older value, its
overlay can set that value explicitly. This reduces copy and merge conflicts;
it does not prevent a changed default from changing behavior when the overlay
does not override it.

The base file is a complete, loadable starting configuration, not an
environment-ready test lab. It has no actual hosts by default, and settings
such as free NICs, storage locations, and installation tools still need
environment-specific values. This distinction is useful: test collection and
type checking do not require a local configuration file, while tests that need
remote infrastructure still need an appropriate overlay or command-line
arguments.

Keeping the defaults in TOML also keeps the type checker independent of local
environment data. The typed `Config` model is part of the project and does not
depend on importing a generated `data.py`. An overlay supplies runtime values;
it does not add a Python configuration module that the type checker must
follow.

## Includes make overlays reusable

Every TOML configuration file can contain a top-level `include` array. The
loader reads included files recursively before applying the content of the
file that names them:

```toml
include = ["common.toml", "datacenters/cgt1.toml"]

[tools.update]
hosting_pool = "10.30.0.50"
```

Include paths are resolved relative to the including file first, then relative
to the xcp-ng-tests repository root. Dictionaries are deep-merged. A later
include overrides an earlier include, and the including file overrides all of
its includes. Lists and scalar values are replaced rather than appended.
Cyclic includes are rejected. A file included through two different paths is
allowed as long as the paths do not form a cycle.

This lets several overlays share common settings without copying them. For
example, credentials and repository defaults can live in one file, datacenter
settings in another, and an environment overlay can contain only the values
specific to that environment. The base `lib/config.toml` is always loaded
first with the lowest precedence, so an overlay does not need to include it
explicitly.

The include directive is a loader instruction, not a field in the final
`Config` object. It is described by the editor schema so that editors can
complete and validate it, then removed before model validation.

## Value sources and precedence

The loader combines sources from lowest to highest precedence:

1. `lib/config.toml` provides the defaults, together with any files it
   includes.
2. A selected overlay and everything it includes are merged on top. An
   overlay can be selected with `--config` in `pytest`, or with `-c/--config`
   in `tools.py` and `jobs.py`. `XCPNG_CONFIG` is the environment-variable
   equivalent. If no overlay is selected, the local overlay
   `config.local.toml` is merged on top when it exists. This is an implicit
   overlay selection, not an additional configuration source alongside a
   selected overlay.
3. `XCPNG_TESTS_*` environment variables override values from the files.
4. Repeatable `--config-value KEY=VALUE` options override the environment
   variables. Later options for the same key win.

The file configuration sources are useful for values that should be shared or
reviewed. The environment and command-line configuration sources are useful
for a CI job, a local machine, or one test run that needs a small change
without modifying a file.

Environment variable names use `__` for nested keys. Names and key segments
are case-sensitive. Values are parsed as TOML when possible, so strings,
numbers, booleans, arrays, and inline tables can be supplied directly:

```bash
XCPNG_TESTS_network__free_nics='["eth1", "eth2"]'
```

An unquoted value such as `123` is parsed as an integer. Quote it when the
configuration field expects a string.

`--config-value` uses a dotted key path. Double quotes preserve dots inside a
key, such as an IP address:

```bash
--config-value 'hosts."10.30.0.56".user=root'
```

`--config-value` is the highest source in the config loader. It is not the
highest source for every command: dedicated pytest options such as
`--volume-size`, `--write-volume-cap`, and `--write-volume-align` are applied
after configuration loading. Target-selection options such as `--hosts` are
separate from value precedence.

## Short names select overlays

An overlay can be selected by its path or by a short name. For example, the
short name `cgt1` refers to `config.cgt1.toml`. The resolver checks the value
as a path first. An absolute path is used directly, and a relative path is
checked relative to the current directory. If it does not name an existing
file, the resolver looks for:

1. `XCPNG_CONFIG_DIR/config.cgt1.toml`, when `XCPNG_CONFIG_DIR` is set.
2. `config.cgt1.toml` in the xcp-ng-tests repository root.

The configured directory takes precedence over the repository root. Short-name
lookup does not separately search `config.cgt1.toml` in the current directory
unless that directory is the repository root or is set as
`XCPNG_CONFIG_DIR`.

Short names give an environment a stable identifier instead of requiring an
absolute path in every command or script. `XCPNG_CONFIG_DIR` can point several
worktrees at one shared directory, so the same name selects the same overlay
regardless of which checkout runs the tests. `XCPNG_CONFIG` also accepts a
short name, which makes one selection reusable by commands that support it:

```bash
pytest --config=cgt1
uv run scripts/tools.py update -c cgt1
```

A short name is only a filename lookup convention. It is not a separate kind
of configuration.

## How consumers use the configuration

Configuration values, target selection, and job definitions are separate
concerns. The common TOML model supplies environment data, but each entry
point decides which parts it consumes and which command-line options override
them.

### `pytest`

At pytest setup time, the selected TOML configuration is loaded and applied to
the shared `Config` object. The dedicated pytest options for SSH output and
storage-test sizes are then applied on top of it.

If `--hosts` is provided, its values are the target pool masters. Without it,
pytest uses the keys of the configuration's `[hosts]` table. The host list is
therefore target selection, not another configuration source. Host credentials
and per-host settings are read from `[host]` and `[hosts]` as described in the
reference below.

The repository-specific pytest selector is `--config`. The `-c` option
provided by pytest itself is not the overlay selector used here.

### `jobs.py`

Job definitions and VM references remain in Python. `jobs.py` uses `vm_data.py`
for the VM matrix, and the job parameters in `jobs.py` are converted into
pytest arguments.

For `jobs.py run`, an explicitly supplied positional hosts argument wins. When
it is omitted, the command reads pool masters from the selected configuration's
`[hosts]` table, then forwards `--config` and `--config-value` to the pytest
process. These options must appear before the positional job and hosts
arguments so that `jobs.py` parses them instead of forwarding them as pytest
arguments.

`jobs.py collect` and `jobs.py check` are consistency checks around the Python
job definitions. They invoke pytest to collect tests, but do not select target
hosts or run an environment-specific job. The TOML configuration is relevant
to `jobs.py run`, where it supplies the default hosts and is forwarded to the
pytest process.

### `tools.py` and its inventory

`tools.py` does not use `vm_data.py`. It reads a separate inventory TOML file,
selected with `-c/--config`, and uses the same loader, includes, overlays, and
schema as the other configuration files. When `update`, `clean`, or `exec` is
not given explicit hosts, the tools build an inventory from that file:

- `[tools.update]` supplies repository and hosting-pool defaults.
- The keys of `[hosts]` in the inventory file supply pool-master targets.
- Values in `[hosts."host"]` override the corresponding update defaults for
  that host.
- An empty per-host list is an explicit empty value, not a request to inherit
  the default list.

`-H/--hosts` bypasses the config-derived inventory. For `update`, `-e/-x/-P`
override repositories, disabled repositories, and the hosting pool after the
inventory is built.

Inventory construction and host operations must use the same effective
configuration. In particular, selecting an overlay must select its inventory
values and its credentials together; an inventory-only selector would make it
possible to target one environment with another environment's credentials.
This is why configuration selection is a shared concern of the tools command
and its lower-level host helpers, rather than a setting belonging only to the
inventory.

## Validation and normalization

The design has two related validation contracts.

### Editor schema

`config-schema.json` is generated from the Pydantic `Config` model by
`uv run scripts/gen-config-schema.py`. It is intended for editor completion and
early feedback:

- All fields are optional because an overlay only contains values it changes.
- Most objects reject unknown keys with `additionalProperties = false`.
- `$schema` and `include` are documented as loader-level properties even
  though they are not model fields.
- `net-url` and `net-only` are the TOML names of aliased ISO fields.
- Answer-file definitions intentionally allow additional attributes.

The schema validates the shape of an individual file. It does not require a
partial overlay to contain every field in the final configuration.

### Runtime model

After all file and value configuration sources are merged, Pydantic validates the final
configuration before the tests start. Wrong types and missing required values
therefore fail early, rather than being discovered after a test in a suite
that may run for a quite long time. Runtime models warn about unknown fields
and ignore them rather than failing; the editor schema is stricter so that
editors can flag those fields earlier.

This is structural validation. It does not prove that a PXE server is
reachable, an ISO or cache directory exists, an SR UUID is valid, a storage
export is available, or an executable path works. Those checks happen when the
relevant test or tool uses the value.

The model also normalizes a few values:

- `volume_size` and `write_volume_cap` accept human-readable sizes such as
  `1 GiB` and store byte counts.
- `write_volume_align` is an integer in the TOML model. Its dedicated pytest
  option also accepts a human-readable size before conversion.
- An empty `objects_name_prefix` becomes `None`.
- `host.default_password_hash` is generated from `host.default_password` at
  load time. A supplied hash is not authoritative.
- `<PASSWORD_HASH>` in answer-file contents is replaced with that generated
  hash.

## Configuration reference

`lib/config.toml` and `config-schema.json` are the authoritative examples and
field reference. The sections below describe the purpose of each part of the
model and its main consumers.

### Root values

- `objects_name_prefix` prefixes the labels of XAPI objects created by tests.
- `dns_server` identifies the DNS server used by relevant VM tests.
- `volume_size` and `write_volume_cap` control storage-test volume sizes and
  write limits.
- `write_volume_align` controls the block alignment used by volume-write
  tests.

### `[host]` and `[hosts]`

- `[host]` contains default XAPI credentials. These credentials are not the
  SSH login credentials; SSH commands connect as `root` using the runner's SSH
  setup.
- `[hosts]` is keyed by the address of each pool's master host. Its entries
  can override `user`, `password`, and `skip_xo_config`, as well as
  repositories, disabled repositories, and `hosting_pool` for the tools
  inventory.
- Per-host `user` and `password` values fall back to the corresponding values
  in `[host]` when omitted.
- The keys are the default pytest targets, and the default tools targets when
  they are present in the inventory TOML file and no explicit hosts are given.

### `[tools.update]`

This table contains update and inventory defaults: `repositories`,
`disabled_repositories`, and the optional `hosting_pool`. In an inventory TOML
file, values set on an entry in `[hosts]` override these defaults for that
host.

### `[network]` and `[pxe]`

- `[network]` contains the management network description and the list of
  NICs available for network tests.
- `[pxe]` contains the configuration server used for automated installations
  and the server queried for ARP information.

### `[vm]`

- `def_url` is the base URL for image values that are not already full URLs.
- `cache_imported` controls whether imported VMs are cached and cloned for
  test modules.
- `default_sr` selects the pool default SR, the first local SR, or an explicit
  SR UUID for imported VM disks.
- `[vm.images]` maps names used by fixtures to image filenames or URLs.
- `[vm.equivalents]` maps image IDs that may substitute for one another when
  caching or deduplicating images.

The VM names and version filters used by jobs remain in `vm_data.py`.

### `[install]`

- `[install.answerfiles]` contains answer-file trees. These definitions allow
  extra answer-file attributes because they describe installer data rather
  than the configuration model itself.
- `[install.isos]` contains the ISO base URL, cache directory, and named ISO
  definitions.
- ISO definitions accept `path`, `net-url`, `net-only`, and `unsigned`.
- `iso_remaster` identifies the optional ISO remastering utility.

### `[guest_tools]`

- `download_url` is the location from which guest-tool ISOs are downloaded.
- `[guest_tools.win]` maps names to Windows guest-tool ISO definitions,
  including the package and XenClean paths.
- `[guest_tools.other]` describes the ISO containing other guest tools.
- `[guest_tools.installed]` describes the individual tools found in that ISO.

### `[xo]` and `[ssh]`

- `[xo].cli` identifies the `xo-cli` executable used by the tests.
- `[ssh].pubkey` contains public keys installed into test images during
  installation.
- `[ssh].output_max_lines` limits SSH output in logs, and
  `[ssh].ignore_banner` controls SSH banner handling.

### `[storage]`

The storage tables are optional device configurations consumed by fixtures
when they request a matching storage type:

- `nfs` and `nfs4` describe remote NFS exports.
- `nfs_iso` and `cifs_iso` describe ISO repositories.
- `cephfs` and `moosefs` describe remote filesystem storage.
- `lvmohba` and `lvmoiscsi` describe block-storage parameters.
- `linstor` contains LINSTOR settings such as redundancy.

The table is not a list of mandatory backends. Local ext, XFS, and ZFS tests
obtain their device information in other ways and do not need corresponding
TOML sections.

### Loader and editor directives

- `include` lists files to load and merge before the current file.
- `$schema` points editors at `config-schema.json` and is removed before
  runtime validation.

## Inspection, migration, and security

### Inspecting the effective configuration

`tools.py dump-config` loads the base configuration, the selected overlay,
includes, and runtime value overrides. By default it removes values equal to
the base `lib/config.toml`; `--all` keeps those values too. TOML output omits null
values, while `--json` produces machine-readable output.

`tools.py diff-config A B` loads two paths or short names independently and
prints a unified diff between their base-plus-overlay configurations. It does
not apply `XCPNG_TESTS_*` or `--config-value` overrides. It ignores generated
password hashes for comparison, but it does not redact plaintext passwords or
other secrets. The command exits with status 0 when the configurations match
and 1 when they differ.

### Migrating `data.py`

`tools.py migrate-data-py file.py` converts known legacy settings into a TOML
file. By default it writes a delta-only overlay, normally
`config.local.toml`; `--all` keeps values that match `lib/config.toml` too.
The converter does not validate the generated file after writing it. Load the
result as an overlay, for example with `dump-config`, before relying on it.

Configuration files and included files may contain host passwords, per-host
credentials, CIFS passwords, storage secrets, answer-file contents, and other
sensitive values. Keep local overlays out of source control and protect their
permissions. `dump-config` and `diff-config` are inspection tools, not secret
redaction tools. In particular, `--all` and diff output can expose values that
were not present in the base file.

## Migration boundary and future organization

The TOML design replaces the environment-specific configuration previously
held in `data.py`. The VM test matrix in `vm_data.py`, the job definitions in
`jobs.py`, and test-specific Python logic remain separate because they describe
test behavior rather than environment values.

A possible future `ci-configuration` repository could use the composition
mechanism like this:

- `base.toml` for settings shared by every environment overlay, such as host
  credentials, guest-tool catalogs, storage servers, and repository defaults.
- `dc/{lyon,lyon-v6,cgt1}.toml` for datacenter network, installation, and
  storage settings.
- `config.<version>.toml` for named environment overlays that include a base
  and a datacenter file, then define their environment-specific values.
- `jobs/` for overlays that add `[hosts]` or other job-specific environment
  values.

This organization is a possible deployment of the design, not a requirement
of the loader. Each migrated overlay can be compared with its legacy
`data.py` result using `diff-config` after the conversion has been validated.
