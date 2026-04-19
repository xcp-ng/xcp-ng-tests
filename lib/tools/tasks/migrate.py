"""Migrate a legacy data.py file to a TOML config file.

Can be run with: uv run scripts/tools.py migrate-data-py [DATA_PY]
"""
from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

import tomli_w

from lib.typing import ConfigDict, JSONType

from typing import Any, overload

_REPO_ROOT = Path(__file__).resolve().parents[3]


def normalize_dict_keys(d: ConfigDict) -> ConfigDict:
    """Normalize dict keys by replacing dashes with underscores."""
    return {k.replace("-", "_"): v for k, v in d.items()}


def load_base_config(repo_root: Path) -> ConfigDict:
    """Load the base config.toml to compare against."""
    config_path = repo_root / "config.toml"
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def load_data_py(data_py_path: Path, repo_root: Path) -> ConfigDict:
    """Load data.py and extract configuration as dict.

    Tries to import as a module first, falls back to exec() for files with custom code.
    This allows static type checkers to work even if data.py doesn't exist.
    """
    namespace: dict[str, Any] = {}

    # Add repo root to path so imports work
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    # Try to load as a module using importlib
    try:
        spec = importlib.util.spec_from_file_location("data", data_py_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load spec for {data_py_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Extract all non-private attributes
        namespace = {
            k: v for k, v in vars(module).items()
            if not k.startswith("_")
        }
    except Exception as e:
        # Fallback: execute as raw Python code
        print(f"WARNING: importing {data_py_path} failed ({e}); falling back to exec", file=sys.stderr)
        with open(data_py_path) as f:
            code = f.read()
        try:
            exec(code, namespace)
        except Exception as e:
            raise ValueError(f"Failed to execute {data_py_path}: {e}") from e

    # Map data.py variable names to config structure
    config: ConfigDict = {}

    # Root-level fields
    if "OBJECTS_NAME_PREFIX" in namespace:
        config["objects_name_prefix"] = namespace["OBJECTS_NAME_PREFIX"] or ""
    if "TEST_DNS_SERVER" in namespace:
        config["dns_server"] = namespace["TEST_DNS_SERVER"]

    # Host section
    host_config: ConfigDict = {}
    # Support both DEFAULT_USER and HOST_DEFAULT_USER naming conventions
    if "HOST_DEFAULT_USER" in namespace:
        host_config["default_user"] = namespace["HOST_DEFAULT_USER"]
    elif "DEFAULT_USER" in namespace:
        host_config["default_user"] = namespace["DEFAULT_USER"]
    # Support both DEFAULT_PASSWORD and HOST_DEFAULT_PASSWORD naming conventions
    if "HOST_DEFAULT_PASSWORD" in namespace:
        host_config["default_password"] = namespace["HOST_DEFAULT_PASSWORD"]
    elif "DEFAULT_PASSWORD" in namespace:
        host_config["default_password"] = namespace["DEFAULT_PASSWORD"]
    config["host"] = host_config

    # Hosts section (per-host overrides)
    if "HOSTS" in namespace:
        config["hosts"] = namespace["HOSTS"]

    # Network section
    network_config: ConfigDict = {}
    if "NETWORKS" in namespace and "MGMT" in namespace["NETWORKS"]:
        network_config["mgmt"] = namespace["NETWORKS"]["MGMT"]
    if "HOST_FREE_NICS" in namespace and namespace["HOST_FREE_NICS"]:
        network_config["free_nics"] = namespace["HOST_FREE_NICS"]
    if network_config:
        config["network"] = network_config

    # PXE section
    pxe_config: ConfigDict = {}
    if "PXE_CONFIG_SERVER" in namespace:
        pxe_config["config_server"] = namespace["PXE_CONFIG_SERVER"]
    if "ARP_SERVER" in namespace:
        pxe_config["arp_server"] = namespace["ARP_SERVER"]
    if pxe_config:
        config["pxe"] = pxe_config

    # VM section
    vm_config: ConfigDict = {}
    if "DEF_VM_URL" in namespace:
        vm_config["def_url"] = namespace["DEF_VM_URL"]
    if "CACHE_IMPORTED_VM" in namespace:
        vm_config["cache_imported"] = namespace["CACHE_IMPORTED_VM"]
    if "DEFAULT_SR" in namespace:
        vm_config["default_sr"] = namespace["DEFAULT_SR"]

    # VM images
    if "VM_IMAGES" in namespace:
        vm_config["images"] = namespace["VM_IMAGES"]

    # VM equivalences
    if "IMAGE_EQUIVS" in namespace:
        vm_config["equivalents"] = namespace["IMAGE_EQUIVS"]
    if vm_config:
        config["vm"] = vm_config

    # Install section
    install_config: ConfigDict = {}
    if "BASE_ANSWERFILES" in namespace:
        install_config["answerfiles"] = namespace["BASE_ANSWERFILES"]

    # Install ISOs
    isos_config: ConfigDict = {}
    if "ISO_IMAGES_BASE" in namespace:
        isos_config["base_url"] = namespace["ISO_IMAGES_BASE"]
    if "ISO_IMAGES_CACHE" in namespace:
        isos_config["cache_dir"] = namespace["ISO_IMAGES_CACHE"]
    if "ISO_IMAGES" in namespace:
        isos_config["definitions"] = namespace["ISO_IMAGES"]
    if isos_config:
        install_config["isos"] = isos_config
    if install_config:
        config["install"] = install_config

    # Guest tools section
    guest_tools_config: ConfigDict = {}
    if "ISO_DOWNLOAD_URL" in namespace:
        guest_tools_config["download_url"] = namespace["ISO_DOWNLOAD_URL"]
    if "WIN_GUEST_TOOLS_ISOS" in namespace:
        guest_tools_config["win"] = namespace["WIN_GUEST_TOOLS_ISOS"]
    if "OTHER_GUEST_TOOLS_ISO" in namespace:
        guest_tools_config["other"] = namespace["OTHER_GUEST_TOOLS_ISO"]
    if "OTHER_GUEST_TOOLS" in namespace:
        guest_tools_config["installed"] = namespace["OTHER_GUEST_TOOLS"]
    if guest_tools_config:
        config["guest_tools"] = guest_tools_config

    # SSH section
    ssh_config: ConfigDict = {}
    if "TEST_SSH_PUBKEY" in namespace:
        ssh_config["pubkey"] = namespace["TEST_SSH_PUBKEY"]
    if "SSH_OUTPUT_MAX_LINES" in namespace:
        ssh_config["output_max_lines"] = namespace["SSH_OUTPUT_MAX_LINES"]
    if "IGNORE_SSH_BANNER" in namespace:
        ssh_config["ignore_banner"] = namespace["IGNORE_SSH_BANNER"]
    if ssh_config:
        config["ssh"] = ssh_config

    # iso_remaster tool path goes into install section
    if "TOOLS" in namespace:
        tools = normalize_dict_keys(namespace["TOOLS"])
        if "iso_remaster" in tools:
            install_config["iso_remaster"] = tools["iso_remaster"]
            config["install"] = install_config
        if "xo_cli" in tools:
            config["xo"] = {"cli": tools["xo_cli"]}

    # Storage section
    storage: ConfigDict = {}
    if "NFS_DEVICE_CONFIG" in namespace:
        storage["nfs"] = namespace["NFS_DEVICE_CONFIG"]
    if "NFS4_DEVICE_CONFIG" in namespace:
        storage["nfs4"] = namespace["NFS4_DEVICE_CONFIG"]
    if "NFS_ISO_DEVICE_CONFIG" in namespace:
        storage["nfs_iso"] = namespace["NFS_ISO_DEVICE_CONFIG"]
    if "CIFS_ISO_DEVICE_CONFIG" in namespace:
        storage["cifs_iso"] = namespace["CIFS_ISO_DEVICE_CONFIG"]
    if "CEPHFS_DEVICE_CONFIG" in namespace:
        storage["cephfs"] = namespace["CEPHFS_DEVICE_CONFIG"]
    if "MOOSEFS_DEVICE_CONFIG" in namespace:
        storage["moosefs"] = namespace["MOOSEFS_DEVICE_CONFIG"]
    if "LVMOHBA_DEVICE_CONFIG" in namespace:
        storage["lvmohba"] = namespace["LVMOHBA_DEVICE_CONFIG"]
    if "LVMOISCSI_DEVICE_CONFIG" in namespace:
        storage["lvmoiscsi"] = namespace["LVMOISCSI_DEVICE_CONFIG"]
    if "LINSTOR_REDUNDANCY" in namespace:
        storage["linstor"] = {"redundancy": namespace["LINSTOR_REDUNDANCY"]}
    if storage:
        config["storage"] = storage

    return config


def deep_dict_equal(d1: JSONType, d2: JSONType) -> bool:
    """Check if two values are deeply equal."""
    if isinstance(d1, dict) and isinstance(d2, dict):
        if set(d1.keys()) != set(d2.keys()):
            return False
        return all(deep_dict_equal(d1[k], d2[k]) for k in d1)
    if isinstance(d1, list) and isinstance(d2, list):
        return len(d1) == len(d2) and all(deep_dict_equal(a, b) for a, b in zip(d1, d2))
    # For non-container types, check both type and value
    return type(d1) is type(d2) and d1 == d2


def _strip_password_hashes(obj: JSONType) -> JSONType:
    """Recursively strip password hashes for comparison purposes.

    - Replaces $6$... hashes with placeholder
    - Replaces <PASSWORD_HASH> placeholder with itself (already normalized)
    - Converts tuples to lists for consistent comparison
    """
    if isinstance(obj, str):
        # If it looks like a password hash (starts with $6$), replace with placeholder
        if obj.startswith("$6$"):
            return "<PASSWORD_HASH>"
        # Placeholder stays as is
        return obj
    if isinstance(obj, dict):
        return {k: _strip_password_hashes(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        # Convert tuples to lists for consistent comparison
        return [_strip_password_hashes(item) for item in obj]
    return obj


def remove_defaults(
    config: ConfigDict, base: ConfigDict
) -> ConfigDict:
    """Remove fields from config that have the same value as in base.

    Ignores password hash differences (strips them for comparison).
    """
    result: ConfigDict = {}

    for key, value in config.items():
        if key not in base:
            # Key not in base, keep it
            result[key] = value
        elif isinstance(value, dict) and isinstance((base_value := base.get(key)), dict):
            # Recursively check nested dicts
            nested = remove_defaults(value, base_value)
            if nested:  # Only add if there's something left
                result[key] = nested
        else:
            # Strip password hashes before comparing
            value_stripped = _strip_password_hashes(value)
            base_stripped = _strip_password_hashes(base.get(key))
            if not deep_dict_equal(value_stripped, base_stripped):
                # Values differ (ignoring password hashes), keep it
                result[key] = value
        # else: values are the same, skip it

    return result


@overload
def _sorted_recursive(value: ConfigDict) -> ConfigDict:
    ...


@overload
def _sorted_recursive(value: JSONType) -> JSONType:
    ...


def _sorted_recursive(value: JSONType) -> JSONType:
    """Recursively sort dict keys for stable output."""
    if isinstance(value, dict):
        return {k: _sorted_recursive(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_sorted_recursive(item) for item in value]
    return value


def write_toml(config: ConfigDict, output_path: Path) -> None:
    """Write config dict to TOML file with $schema attribute."""
    data = {"$schema": "./config-schema.json", **_sorted_recursive(config)}
    with open(output_path, "wb") as f:
        tomli_w.dump(data, f, multiline_strings=True)


def migrate_data_py(
    data_py: Path | str | None = None,
    output: str = "config.default.toml",
    force: bool = False,
) -> int:
    """Convert a legacy data.py file to a TOML config file.

    DATA_PY   Optional path to the data.py file to migrate. Defaults to
              <repo_root>/data.py. May be an absolute or relative path.
    output    Output file name. If relative, resolved against <repo_root>.
              Defaults to config.default.toml.
    force     Overwrite the output file if it already exists.
    """
    repo_root = _REPO_ROOT
    if data_py is not None:
        data_py_path = (repo_root / data_py).resolve()
    else:
        data_py_path = repo_root / "data.py"

    # Validate input file
    if not data_py_path.exists():
        print(f"ERROR: {data_py_path} not found", file=sys.stderr)
        return 1

    output_path_raw = Path(output)
    output_path = output_path_raw if output_path_raw.is_absolute() else repo_root / output

    # Check if output file exists
    if output_path.exists() and not force:
        print(
            f"ERROR: {output_path} already exists. Use --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    # Load base config and data.py
    try:
        base_config = load_base_config(repo_root)
    except Exception as e:
        print(f"ERROR: Failed to load base config.toml: {e}", file=sys.stderr)
        return 1

    try:
        data_config = load_data_py(data_py_path, repo_root)
    except Exception as e:
        print(f"ERROR: Failed to load {data_py_path}: {e}", file=sys.stderr)
        return 1

    # Remove defaults
    override_config = remove_defaults(data_config, base_config)

    if not override_config:
        print(
            f"INFO: No differences found between {data_py_path} and config.toml",
            file=sys.stderr,
        )
        print(f"      {output_path} would be empty, not creating file", file=sys.stderr)
        return 0

    # Write output
    try:
        write_toml(override_config, output_path)
        print(f"✓ Created {output_path}", file=sys.stdout)
        return 0
    except Exception as e:
        print(f"ERROR: Failed to write {output_path}: {e}", file=sys.stderr)
        return 1
