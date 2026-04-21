from __future__ import annotations

import sys
import tomllib
import warnings
from pathlib import Path

from passlib.hash import sha512_crypt  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, field_validator

from typing import Any

def hash_password(password: str) -> str:
    """Hash password for /etc/password."""
    return sha512_crypt.using(rounds=5000).hash(password)

class HostConfig(BaseModel):
    default_user: str
    default_password: str
    default_password_hash: str = ""

    @field_validator("default_password_hash", mode="before")
    @classmethod
    def compute_password_hash(cls, v: str, info: Any) -> str:
        if not v and "default_password" in info.data:
            import legacycrypt as crypt  # type: ignore[import-untyped]
            password = info.data["default_password"]
            salt = crypt.mksalt(crypt.METHOD_SHA512)  # type: ignore
            return crypt.crypt(password, salt)  # type: ignore
        return v


class NetworkConfig(BaseModel):
    mgmt: str = "Pool-wide network associated with eth0"


class PXEConfig(BaseModel):
    config_server: str
    arp_server: str


class VMImagesConfig(BaseModel):
    model_config = {"extra": "allow"}


class VMConfig(BaseModel):
    def_url: str
    cache_imported: bool
    default_sr: str
    images: VMImagesConfig = Field(default_factory=VMImagesConfig)
    equivalents: dict[str, str] = Field(default_factory=dict)


class InstallIsosConfig(BaseModel):
    base_url: str
    cache_dir: str
    definitions: dict[str, dict[str, Any]] = Field(default_factory=dict)


class InstallConfig(BaseModel):
    answerfiles: dict[str, Any] = Field(default_factory=dict)
    isos: InstallIsosConfig
    iso_remaster: str = ""


class GuestToolsConfig(BaseModel):
    download_url: str
    win: dict[str, dict[str, Any]] = Field(default_factory=dict)
    other: dict[str, Any] = Field(default_factory=dict)
    installed: dict[str, dict[str, Any]] = Field(default_factory=dict)


class SSHConfig(BaseModel):
    pubkey: str
    output_max_lines: int = 20
    ignore_banner: bool = False


class StorageConfig(BaseModel):
    nfs: dict[str, str] = Field(default_factory=dict)
    nfs4: dict[str, str] = Field(default_factory=dict)
    nfs_iso: dict[str, str] = Field(default_factory=dict)
    cifs_iso: dict[str, str] = Field(default_factory=dict)
    cephfs: dict[str, str] = Field(default_factory=dict)
    moosefs: dict[str, str] = Field(default_factory=dict)
    lvmoiscsi: dict[str, str] = Field(default_factory=dict)


class Config(BaseModel):
    objects_name_prefix: str | None = None
    dns_server: str = "1.1.1.1"
    host: HostConfig
    hosts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    pxe: PXEConfig
    vm: VMConfig
    install: InstallConfig
    guest_tools: GuestToolsConfig
    ssh: SSHConfig
    storage: StorageConfig = Field(default_factory=StorageConfig)

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
        storage_cfg = getattr(self.storage, config_key.replace("_DEVICE_CONFIG", "").lower())
        if not isinstance(storage_cfg, dict):
            storage_cfg = {}
        for required_field in required:
            if required_field not in storage_cfg:
                raise Exception(
                    f"Storage config '{config_key}' lacks mandatory '{required_field}'"
                )
        return storage_cfg


def _load_toml_file(path: Path) -> dict[str, Any]:
    """Load TOML file and return dict."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge override into base (recursive)."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            base[key] = _merge_dicts(base[key], value)
        else:
            base[key] = value
    return base


def _replace_password_hash_placeholder(obj: Any, password_hash: str) -> Any:
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
            "Please run: uv run scripts/migrate_data_py.py\n"
            f"And then remove {data_py_path}",
            UserWarning,
            stacklevel=2,
        )


def _build_config(base_data: dict[str, Any]) -> Config:
    """Apply password hash replacement, validate with Pydantic, and return Config."""
    if "host" in base_data and isinstance(base_data["host"], dict):
        password = base_data["host"].get("default_password", "")
        password_hash = hash_password(password)
        base_data = _replace_password_hash_placeholder(base_data, password_hash)
    try:
        return Config(**base_data)
    except Exception as e:
        print(f"FATAL: Config validation failed:\n{e}", file=sys.stderr)
        sys.exit(1)


def load_config() -> Config:
    """Load config.toml from repo root. Validate with Pydantic and return Config."""
    repo_root = Path(__file__).parent.parent
    base_config_path = repo_root / "config.toml"
    try:
        base_data = _load_toml_file(base_config_path)
    except FileNotFoundError:
        print(f"FATAL: {base_config_path} not found", file=sys.stderr)
        sys.exit(1)
    return _build_config(base_data)


def apply_override(config_name: str) -> None:
    """Load config.toml, merge config.{config_name}.toml on top, update config in place."""
    repo_root = Path(__file__).parent.parent
    base_config_path = repo_root / "config.toml"
    try:
        base_data = _load_toml_file(base_config_path)
    except FileNotFoundError:
        print(f"FATAL: {base_config_path} not found", file=sys.stderr)
        sys.exit(1)
    override_path = repo_root / f"config.{config_name}.toml"
    if not override_path.exists():
        print(f"FATAL: {override_path} not found", file=sys.stderr)
        sys.exit(1)
    base_data = _merge_dicts(base_data, _load_toml_file(override_path))
    new = _build_config(base_data)
    for field in new.model_fields:
        setattr(config, field, getattr(new, field))


def sr_device_config(
    config_key: str, *, required: list[str] | None = None
) -> dict[str, str]:
    """Delegate to config.sr_device_config() for backward compat."""
    return config.sr_device_config(config_key, required=required)


config: Config = load_config()
