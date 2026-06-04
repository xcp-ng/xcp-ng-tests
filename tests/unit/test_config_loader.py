from __future__ import annotations

import pytest

from pathlib import Path

from passlib.hash import sha512_crypt

from lib.config_loader import (
    ConfigError,
    _build_config,
    _load_toml_file,
    _load_toml_with_includes,
    load_config,
)

from typing import Any

REPO_ROOT = Path(__file__).parents[2]


def _full_config_dict() -> dict[str, Any]:
    return load_config().model_dump()


def test_base_config_loads() -> None:
    cfg = load_config()
    assert cfg.host.default_user == "root"
    assert cfg.pxe.config_server == "pxe"
    assert cfg.volume_size == 2**30
    assert cfg.network.free_nics == []


def test_schema_key_is_ignored() -> None:
    assert "$schema" not in _load_toml_file(REPO_ROOT / "config.toml")
    assert "$schema" not in _full_config_dict()


def test_default_password_hash_matches_password() -> None:
    cfg = load_config()
    assert sha512_crypt.verify(cfg.host.default_password, cfg.host.default_password_hash)


def test_unknown_section_rejected() -> None:
    data = _full_config_dict()
    data["not_a_section"] = 1
    with pytest.raises(ConfigError):
        _build_config(data)


def test_unknown_key_rejected() -> None:
    data = _full_config_dict()
    data["host"]["defalt_password"] = "typo"
    with pytest.raises(ConfigError):
        _build_config(data)


def test_unknown_host_override_key_rejected() -> None:
    data = _full_config_dict()
    data["hosts"]["1.2.3.4"] = {"pasword": "typo"}
    with pytest.raises(ConfigError):
        _build_config(data)


def test_unknown_storage_key_rejected() -> None:
    data = _full_config_dict()
    data["storage"]["lvmoiscsi"]["targetIQN"] = "ok"
    data["storage"]["lvmoiscsi"]["SCSIid"] = "ok"
    data["storage"]["lvmoiscsi"]["targetiqn"] = "typo"
    with pytest.raises(ConfigError):
        _build_config(data)


def test_answerfiles_allow_extra_keys() -> None:
    data = _full_config_dict()
    data["install"]["answerfiles"]["INSTALL"]["mode"] = "upgrade"
    assert _build_config(data).install.answerfiles["INSTALL"].model_dump()["mode"] == "upgrade"


def test_storage_device_config_delta() -> None:
    data = _full_config_dict()
    data["storage"]["nfs"] = {"server": "10.0.0.2", "serverpath": "/vms"}
    cfg = _build_config(data)
    assert cfg.sr_device_config("NFS_DEVICE_CONFIG") == {"server": "10.0.0.2", "serverpath": "/vms"}
    assert cfg.sr_device_config("CIFS_ISO_DEVICE_CONFIG") == {}
    with pytest.raises(ConfigError):
        cfg.sr_device_config("NFS_ISO_DEVICE_CONFIG", required=["location"])


def test_include_merge(tmp_path: Path) -> None:
    (tmp_path / "base.toml").write_text("a = 1\n")
    (tmp_path / "overlay.toml").write_text('include = ["base.toml"]\nb = 2\n')
    assert _load_toml_with_includes(tmp_path / "overlay.toml") == {"a": 1, "b": 2}


def test_include_cycle_detected(tmp_path: Path) -> None:
    (tmp_path / "a.toml").write_text('include = ["b.toml"]\n')
    (tmp_path / "b.toml").write_text('include = ["a.toml"]\n')
    with pytest.raises(ConfigError):
        _load_toml_with_includes(tmp_path / "a.toml")


def test_include_diamond_allowed(tmp_path: Path) -> None:
    (tmp_path / "d.toml").write_text("d = 1\n")
    (tmp_path / "b.toml").write_text('include = ["d.toml"]\nb = 1\n')
    (tmp_path / "c.toml").write_text('include = ["d.toml"]\nc = 1\n')
    (tmp_path / "a.toml").write_text('include = ["b.toml", "c.toml"]\n')
    assert _load_toml_with_includes(tmp_path / "a.toml") == {"d": 1, "b": 1, "c": 1}


def test_env_override_parses_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('XCPNG_TESTS_network__free_nics', '["eth1"]')
    assert load_config().network.free_nics == ["eth1"]


def test_env_override_preserves_key_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XCPNG_TESTS_storage__lvmoiscsi__SCSIid", '"wwn-1234567890abcdef"')
    assert load_config().storage.lvmoiscsi.SCSIid == "wwn-1234567890abcdef"


def test_env_override_preserves_value_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XCPNG_TESTS_network__mgmt", '"MgmtNet"')
    assert load_config().network.mgmt == "MgmtNet"
