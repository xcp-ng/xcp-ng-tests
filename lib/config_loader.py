from __future__ import annotations

import os
import tomllib
import warnings
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from lib.passwords import hash_password
from lib.sizes import parse_size
from lib.typing import ConfigDict, JSONType

from typing import overload

class ConfigError(Exception):
    """Raised when the TOML configuration cannot be loaded or validated."""


class _StrictModel(BaseModel):
    """Reject unknown keys at load time (extra="forbid")."""
    model_config = {"extra": "forbid"}


REPO_ROOT = Path(__file__).resolve().parent.parent


class HostConfig(_StrictModel):
    default_user: str
    default_password: str
    default_password_hash: str = ""


class HostOverride(_StrictModel):
    user: str | None = None
    password: str | None = None
    skip_xo_config: bool | None = None
    repositories: list[str] | None = None
    disabled_repositories: list[str] | None = None
    hosting_pool: str | None = None


class NetworkConfig(_StrictModel):
    mgmt: str
    free_nics: list[str]


class PXEConfig(_StrictModel):
    config_server: str
    arp_server: str


class VMConfig(_StrictModel):
    def_url: str
    cache_imported: bool
    default_sr: str
    images: dict[str, str]
    equivalents: dict[str, str]


class IsoImageDef(_StrictModel):
    path: str
    net_url: str | None = Field(default=None, alias="net-url")
    net_only: bool | None = Field(default=None, alias="net-only")
    unsigned: bool | None = None

    model_config = {"populate_by_name": True}


class InstallIsosConfig(_StrictModel):
    base_url: str
    cache_dir: str
    definitions: dict[str, IsoImageDef]


class AnswerFileDef(_StrictModel):
    model_config = {"extra": "allow"}

    TAG: str
    CONTENTS: str | list[AnswerFileDef] | None = None


class InstallConfig(_StrictModel):
    answerfiles: dict[str, AnswerFileDef]
    isos: InstallIsosConfig
    iso_remaster: str = ""


class WinGuestToolDef(_StrictModel):
    name: str
    download: bool
    package: str
    xenclean_path: str
    testsign_cert: str | None = None
    onboard_family: str | None = None


class OtherGuestToolDef(_StrictModel):
    name: str
    download: bool


class InstalledGuestToolDef(_StrictModel):
    type: str | None = None
    path: str | None = None
    package: str | None = None
    testsign_cert: str | None = None
    vendor_device: bool | None = None
    upgradable: bool | None = None
    onboarding_phase: str | None = None


class GuestToolsConfig(_StrictModel):
    download_url: str
    win: dict[str, WinGuestToolDef]
    other: OtherGuestToolDef
    installed: dict[str, InstalledGuestToolDef]


class XOConfig(_StrictModel):
    cli: str


class SSHConfig(_StrictModel):
    pubkey: str
    output_max_lines: int
    ignore_banner: bool


class LinstorConfig(_StrictModel):
    redundancy: int


class NFSConfig(_StrictModel):
    server: str | None = None
    serverpath: str | None = None


class NFS4Config(_StrictModel):
    server: str | None = None
    serverpath: str | None = None
    nfsversion: str | None = None


class NFSISOConfig(_StrictModel):
    location: str | None = None


class CIFSISOConfig(_StrictModel):
    location: str | None = None
    username: str | None = None
    cifspassword: str | None = None
    type: str | None = None
    vers: str | None = None


class CephFSConfig(_StrictModel):
    server: str | None = None
    serverpath: str | None = None


class MooseFSConfig(_StrictModel):
    masterhost: str | None = None
    masterport: str | None = None
    rootpath: str | None = None


class LVMoHBAConfig(_StrictModel):
    SCSIid: str | None = None


class LVMoISCSIConfig(_StrictModel):
    target: str | None = None
    port: str | None = None
    targetIQN: str | None = None
    SCSIid: str | None = None


class StorageConfig(_StrictModel):
    nfs: NFSConfig
    nfs4: NFS4Config
    nfs_iso: NFSISOConfig
    cifs_iso: CIFSISOConfig
    cephfs: CephFSConfig
    moosefs: MooseFSConfig
    lvmohba: LVMoHBAConfig
    lvmoiscsi: LVMoISCSIConfig
    linstor: LinstorConfig


class UpdateDefaults(_StrictModel):
    repositories: list[str] = Field(default_factory=list)
    disabled_repositories: list[str] = Field(default_factory=list)
    hosting_pool: str | None = None


class ToolsConfig(_StrictModel):
    update: UpdateDefaults = Field(default_factory=UpdateDefaults)


class Config(_StrictModel):
    objects_name_prefix: str | None
    dns_server: str
    host: HostConfig
    hosts: dict[str, HostOverride]
    tools: ToolsConfig
    network: NetworkConfig
    pxe: PXEConfig
    vm: VMConfig
    install: InstallConfig
    guest_tools: GuestToolsConfig
    xo: XOConfig
    ssh: SSHConfig
    storage: StorageConfig
    volume_size: int
    write_volume_cap: int
    write_volume_align: int

    @field_validator("volume_size", "write_volume_cap", mode="before")
    @classmethod
    def parse_size_str(cls, v: int | str) -> int:
        if isinstance(v, str):
            return parse_size(v)
        return v

    @field_validator("objects_name_prefix", mode="before")
    @classmethod
    def normalize_objects_name_prefix(cls, v: str | None) -> str | None:
        """Convert empty string to None."""
        return None if v == "" else v

    def sr_device_config(
        self, config_key: str, *, required: list[str] | None = None
    ) -> dict[str, str]:
        """Get storage config by key name. Validate required fields."""
        if required is None:
            required = []
        storage_cfg = getattr(self.storage, config_key.replace("_DEVICE_CONFIG", "").lower(), None)
        if storage_cfg is None:
            return {}
        cfg = storage_cfg.model_dump(exclude_none=True)
        for required_field in required:
            if required_field not in cfg:
                raise ConfigError(
                    f"Storage config '{config_key}' lacks mandatory '{required_field}'"
                )
        return cfg


def _load_toml_file(path: Path) -> ConfigDict:
    """Load TOML file, dropping loader-level pseudo-keys, and return dict."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    data.pop("$schema", None)
    return data


def _require_str_list(value: JSONType, what: str) -> list[str]:
    """Return ``value`` as a list of strings, raising ConfigError otherwise."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{what} must be a list of file paths")
    return [item for item in value if isinstance(item, str)]


def _load_toml_with_includes(
    path: Path,
    _seen: set[Path] | None = None,
    fallback_dir: Path = REPO_ROOT,
) -> ConfigDict:
    """Load a TOML file and recursively merge its includes.

    Files listed in the root-level ``include`` key (array of strings)
    are loaded and deep-merged before the file's own content.
    Includes are resolved relative to the including file's directory first,
    then relative to the main xcp-ng-tests directory (fallback_dir).
    """
    if _seen is None:
        _seen = set()
    path = path.resolve()
    if path in _seen:
        raise ConfigError(f"Cyclic include detected: {path}")
    _seen.add(path)

    try:
        data = _load_toml_file(path)
        includes = _require_str_list(data.pop("include", None) or [], f"'include' in {path}")

        result: ConfigDict = {}
        for inc in includes:
            inc_path = _resolve_include(path.parent, inc, fallback_dir)
            included = _load_toml_with_includes(inc_path, _seen, fallback_dir)
            result = _merge_dicts(result, included)

        return _merge_dicts(result, data)
    finally:
        # Track the recursion stack, not all visited files, so diamond
        # includes (A -> [B, C], B -> D, C -> D) are allowed while true
        # cycles still raise above.
        _seen.discard(path)


def _resolve_include(base_dir: Path, inc: str, fallback_dir: Path) -> Path:
    """Resolve an ``include`` path: relative to base_dir, then fallback_dir."""
    candidate = base_dir / inc
    if candidate.is_file():
        return candidate.resolve()
    candidate = fallback_dir / inc
    if candidate.is_file():
        return candidate.resolve()
    raise FileNotFoundError(
        f"Included config file not found: {inc} (looked in {base_dir} and {fallback_dir})"
    )


def _resolve_config_override(value: str | Path) -> Path:
    """Resolve a -c/--config value to a TOML config file path.

    The value is first tried as given (an absolute path, or relative to the
    current directory). When it does not match a file, it is treated as a
    short name and ``config.NAME.toml`` is looked up in the directory given
    by the XCPNG_CONFIG_DIR env var (when set), then in the xcp-ng-tests
    repository root.
    """
    candidates = [Path(value)]
    if "XCPNG_CONFIG_DIR" in os.environ:
        candidates.append(Path(os.environ["XCPNG_CONFIG_DIR"]) / f"config.{value}.toml")
    candidates.append(REPO_ROOT / f"config.{value}.toml")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        f"Config file not found for {value!r}: "
        f"tried {[str(c) for c in candidates]}"
    )


def _merge_dicts(base: ConfigDict, override: ConfigDict) -> ConfigDict:
    """Deep merge override into base (recursive)."""
    for key, value in override.items():
        existing = base.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            base[key] = _merge_dicts(existing, value)
        else:
            base[key] = value
    return base


def _parse_env_value(raw: str) -> JSONType:
    """Parse env var value as TOML, falling back to plain string."""
    try:
        return tomllib.loads(f"x = {raw}")["x"]
    except tomllib.TOMLDecodeError:
        return raw


def _apply_env_overrides(data: ConfigDict) -> ConfigDict:
    """Override config values from XCPNG_CFG__* env vars."""
    prefix = "XCPNG_TESTS_"
    overrides: ConfigDict = {}
    for key, raw in os.environ.items():
        if not key.startswith(prefix):
            continue
        path = key.removeprefix(prefix).split("__")
        value = _parse_env_value(raw)
        branch = overrides
        for part in path[:-1]:
            node = branch.get(part)
            if not isinstance(node, dict):
                node = {}
                branch[part] = node
            branch = node
        branch[path[-1]] = value
    return _merge_dicts(data, overrides) if overrides else data


@overload
def _replace_password_hash_placeholder(obj: ConfigDict, password_hash: str) -> ConfigDict:
    ...


@overload
def _replace_password_hash_placeholder(obj: JSONType, password_hash: str) -> JSONType:
    ...


def _replace_password_hash_placeholder(obj: JSONType, password_hash: str) -> JSONType:
    """Recursively replace <PASSWORD_HASH> placeholders with actual hash."""
    if isinstance(obj, str):
        return password_hash if obj == "<PASSWORD_HASH>" else obj
    if isinstance(obj, dict):
        return {k: _replace_password_hash_placeholder(v, password_hash) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_password_hash_placeholder(item, password_hash) for item in obj]
    return obj


def warn_legacy_data_py() -> None:
    """Warn if legacy data.py still exists."""
    data_py_path = Path(__file__).parent.parent / "data.py"
    if data_py_path.exists():
        warnings.warn(
            f"Legacy {data_py_path} file found but is NOT used anymore. "
            "Configuration is now loaded from TOML files. "
            "Please run: uv run scripts/tools.py migrate-data-py\n"
            f"And then remove {data_py_path}",
            UserWarning,
            stacklevel=2,
        )


def _build_config(base_data: ConfigDict) -> Config:
    """Apply env overrides and password hash replacement, then validate with Pydantic."""
    base_data = _apply_env_overrides(base_data)
    if "host" in base_data and isinstance(base_data["host"], dict):
        host = base_data["host"]
        password = host.get("default_password", "")
        if not isinstance(password, str):
            raise ConfigError("host.default_password must be a string")
        password_hash = hash_password(password)
        host["default_password_hash"] = password_hash
        base_data = _replace_password_hash_placeholder(base_data, password_hash)
    try:
        return Config.model_validate(base_data)
    except Exception as e:
        raise ConfigError(f"Config validation failed:\n{e}") from e


def load_config(config_path: Path | None = None, override: str | Path | None = None) -> Config:
    """Load and validate a merged TOML config with Pydantic, returning a Config.

    The main config.toml at the repo root is always used as the base (lowest
    priority). An optional override (a .toml path or a profile name) is merged
    on top. When neither an override nor an explicit base path is given,
    config.default.toml at the repo root is auto-merged if it exists.
    Short names are looked up in the XCPNG_CONFIG_DIR directory when it is
    set, then in the xcp-ng-tests repository root.
    """
    base_path = config_path or REPO_ROOT / "config.toml"
    try:
        base_data = _load_toml_with_includes(base_path)
    except FileNotFoundError as e:
        raise ConfigError(f"{e}") from e
    if override is not None:
        try:
            overlay_data = _load_toml_with_includes(_resolve_config_override(override))
        except FileNotFoundError as e:
            raise ConfigError(f"{e}") from e
        base_data = _merge_dicts(base_data, overlay_data)
    elif config_path is None:
        default_path = REPO_ROOT / "config.default.toml"
        if default_path.exists():
            base_data = _merge_dicts(base_data, _load_toml_with_includes(default_path))
    return _build_config(base_data)


def apply_override(config_name: str | None = None) -> None:
    """Load config.toml, merge the overlay (a .toml file path or profile name) on top, update config in place.

    When config_name is None, config.default.toml is auto-merged if it exists.
    Short names are looked up in the XCPNG_CONFIG_DIR directory when it is set,
    then in the xcp-ng-tests repository root.
    """
    new = load_config(override=config_name)
    for field in Config.model_fields:
        setattr(config, field, getattr(new, field))


def sr_device_config(
    config_key: str, *, required: list[str] | None = None
) -> dict[str, str]:
    """Delegate to config.sr_device_config() for backward compat."""
    return config.sr_device_config(config_key, required=required)


config: Config = load_config()
