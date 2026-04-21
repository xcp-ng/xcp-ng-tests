#!/usr/bin/env python3
"""
Convert old data.py to TOML config file.

Usage:
    python scripts/migrate_data_py.py [DATA_PY]
    python scripts/migrate_data_py.py --output config.prod.toml
    python scripts/migrate_data_py.py --force

Options:
    DATA_PY         Path to data.py (default: data.py in repo root)
    --output FILE   Output filename (default: config.default.toml in repo root)
    --force         Overwrite existing output file
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

from typing import Any

def normalize_dict_keys(d: dict[str, Any]) -> dict[str, Any]:
    """Normalize dict keys by replacing dashes with underscores."""
    return {k.replace("-", "_"): v for k, v in d.items()}

def load_base_config(repo_root: Path) -> dict[str, Any]:
    """Load the base config.toml to compare against."""
    config_path = repo_root / "config.toml"
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def load_data_py(data_py_path: Path, repo_root: Path) -> dict[str, Any]:
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
    except Exception:
        # Fallback: execute as raw Python code
        with open(data_py_path) as f:
            code = f.read()
        try:
            exec(code, namespace)
        except Exception as e:
            print(f"ERROR: Failed to execute {data_py_path}: {e}", file=sys.stderr)
            sys.exit(1)

    # Map data.py variable names to config structure
    config: dict[str, Any] = {}

    # Root-level fields
    if "OBJECTS_NAME_PREFIX" in namespace:
        config["objects_name_prefix"] = namespace["OBJECTS_NAME_PREFIX"] or ""
    if "TEST_DNS_SERVER" in namespace:
        config["dns_server"] = namespace["TEST_DNS_SERVER"]

    # Host section
    config["host"] = {}
    # Support both DEFAULT_USER and HOST_DEFAULT_USER naming conventions
    if "HOST_DEFAULT_USER" in namespace:
        config["host"]["default_user"] = namespace["HOST_DEFAULT_USER"]
    elif "DEFAULT_USER" in namespace:
        config["host"]["default_user"] = namespace["DEFAULT_USER"]
    # Support both DEFAULT_PASSWORD and HOST_DEFAULT_PASSWORD naming conventions
    if "HOST_DEFAULT_PASSWORD" in namespace:
        config["host"]["default_password"] = namespace["HOST_DEFAULT_PASSWORD"]
    elif "DEFAULT_PASSWORD" in namespace:
        config["host"]["default_password"] = namespace["DEFAULT_PASSWORD"]

    # Hosts section (per-host overrides)
    if "HOSTS" in namespace:
        config["hosts"] = namespace["HOSTS"]

    # Network section
    if "NETWORKS" in namespace and "MGMT" in namespace["NETWORKS"]:
        config["network"] = {"mgmt": namespace["NETWORKS"]["MGMT"]}

    # PXE section
    config["pxe"] = {}
    if "PXE_CONFIG_SERVER" in namespace:
        config["pxe"]["config_server"] = namespace["PXE_CONFIG_SERVER"]
    if "ARP_SERVER" in namespace:
        config["pxe"]["arp_server"] = namespace["ARP_SERVER"]

    # VM section
    config["vm"] = {}
    if "DEF_VM_URL" in namespace:
        config["vm"]["def_url"] = namespace["DEF_VM_URL"]
    if "CACHE_IMPORTED_VM" in namespace:
        config["vm"]["cache_imported"] = namespace["CACHE_IMPORTED_VM"]
    if "DEFAULT_SR" in namespace:
        config["vm"]["default_sr"] = namespace["DEFAULT_SR"]

    # VM images
    if "VM_IMAGES" in namespace:
        config["vm"]["images"] = namespace["VM_IMAGES"]

    # VM equivalences
    if "IMAGE_EQUIVS" in namespace:
        config["vm"]["equivalents"] = namespace["IMAGE_EQUIVS"]

    # Install section
    install_config: dict[str, Any] = {}
    if "BASE_ANSWERFILES" in namespace:
        install_config["answerfiles"] = namespace["BASE_ANSWERFILES"]

    # Install ISOs
    isos_config: dict[str, Any] = {}
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
    guest_tools_config: dict[str, Any] = {}
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
    ssh_config: dict[str, Any] = {}
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
            if "install" not in config:
                config["install"] = {}
            config["install"]["iso_remaster"] = tools["iso_remaster"]

    # Storage section
    storage: dict[str, dict[str, str]] = {}
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
    if "LVMOISCSI_DEVICE_CONFIG" in namespace:
        storage["lvmoiscsi"] = namespace["LVMOISCSI_DEVICE_CONFIG"]
    if storage:
        config["storage"] = storage

    return config


def deep_dict_equal(d1: Any, d2: Any) -> bool:
    """Check if two values are deeply equal."""
    if isinstance(d1, dict) and isinstance(d2, dict):
        if set(d1.keys()) != set(d2.keys()):
            return False
        return all(deep_dict_equal(d1[k], d2[k]) for k in d1)
    if isinstance(d1, list) and isinstance(d2, list):
        return len(d1) == len(d2) and all(deep_dict_equal(a, b) for a, b in zip(d1, d2))
    # For non-container types, check both type and value
    return type(d1) is type(d2) and d1 == d2


def _strip_password_hashes(obj: Any) -> Any:
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
    config: dict[str, Any], base: dict[str, Any]
) -> dict[str, Any]:
    """Remove fields from config that have the same value as in base.

    Ignores password hash differences (strips them for comparison).
    """
    result: dict[str, Any] = {}

    for key, value in config.items():
        if key not in base:
            # Key not in base, keep it
            result[key] = value
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            # Recursively check nested dicts
            nested = remove_defaults(value, base[key])
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


def format_toml_value(value: Any) -> str:
    """Format a Python value as TOML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        # Use multiline string (triple quotes) if string contains newlines
        if "\n" in value:
            # Use triple-quoted string - no need to escape newlines
            # Ensure the string doesn't end with quotes
            if value.endswith('"""'):
                # Escape the trailing quotes by using a regular string
                escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                return f'"{escaped}"'
            return f'"""{value}"""'
        else:
            # Single line - escape special characters
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        # Convert tuples to lists for TOML
        items = [format_toml_value(item) for item in value]
        return "[" + ", ".join(items) + "]"
    if isinstance(value, dict):
        # For inline tables - quote keys that contain special characters
        items = []
        for k, v in value.items():
            # Quote key if it's not a bare word (contains non-alphanumeric except _ and -)
            if any(not (c.isalnum() or c in "_-") for c in k):
                quoted_key = f'"{k}"'
            else:
                quoted_key = k
            items.append(f'{quoted_key} = {format_toml_value(v)}')
        return "{" + ", ".join(items) + "}"
    return str(value)


def write_toml(config: dict[str, Any], output_path: Path) -> None:
    """Write config dict to TOML file with $schema attribute.

    Uses proper TOML table sections for nested dicts to avoid inline tables.
    Storage and other subsections use [section.subsection] format.
    """
    lines: list[str] = []

    # Add $schema at the top for editor validation (must be quoted)
    lines.append('"$schema" = "./config-schema.json"')

    def quote_key_if_needed(key: str) -> str:
        """Quote key if it's not a bare word (contains non-alphanumeric except _ and -)."""
        if any(not (c.isalnum() or c in "_-") for c in key):
            return f'"{key}"'
        return key

    def write_section(
        path: str, data: dict[str, Any], parent_is_simple: bool = False
    ) -> None:
        """Recursively write section and subsections."""
        # Separate simple values from nested dicts
        simple_values = {k: v for k, v in data.items() if not isinstance(v, dict)}
        nested_dicts = {k: v for k, v in data.items() if isinstance(v, dict)}

        # Write simple values in this section
        if simple_values:
            lines.append("")
            lines.append(f"[{path}]")
            for key, value in simple_values.items():
                quoted_key = quote_key_if_needed(key)
                lines.append(f"{quoted_key} = {format_toml_value(value)}")

        # Recursively write nested sections
        for key, value in nested_dicts.items():
            quoted_key = quote_key_if_needed(key)
            write_section(f"{path}.{quoted_key}", value, parent_is_simple=False)

    for section, section_data in config.items():
        if isinstance(section_data, dict):
            write_section(section, section_data)
        else:
            lines.append("")
            lines.append(f"{section} = {format_toml_value(section_data)}")

    # Write to file
    with open(output_path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    """Main entry point.

    Usage: migrate_data_py.py [DATA_PY] [--output OUTPUT] [--force]

    DATA_PY   Optional path to the data.py file to migrate. Defaults to
              <repo_root>/data.py. May be an absolute or relative path.
    --output  Output file path. If relative, resolved against <repo_root>.
              Defaults to config.default.toml.
    --force   Overwrite the output file if it already exists.
    """
    output_file = "config.default.toml"
    force = False
    data_py_arg: str | None = None

    # Parse options
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--output" and i + 1 < len(sys.argv):
            output_file = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--force":
            force = True
            i += 1
        elif not sys.argv[i].startswith("-"):
            if data_py_arg is not None:
                print("ERROR: Too many positional arguments", file=sys.stderr)
                return 1
            data_py_arg = sys.argv[i]
            i += 1
        else:
            print(f"ERROR: Unknown option {sys.argv[i]}", file=sys.stderr)
            return 1

    repo_root = Path(__file__).parent.parent
    if data_py_arg is not None:
        data_py_path = Path(data_py_arg).resolve()
    else:
        data_py_path = repo_root / "data.py"

    # Validate input file
    if not data_py_path.exists():
        print(f"ERROR: {data_py_path} not found", file=sys.stderr)
        return 1

    output_path_raw = Path(output_file)
    output_path = output_path_raw if output_path_raw.is_absolute() else repo_root / output_file

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

    data_config = load_data_py(data_py_path, repo_root)

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


if __name__ == "__main__":
    sys.exit(main())
