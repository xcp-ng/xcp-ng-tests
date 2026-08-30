from __future__ import annotations

import pytest

import logging
from pathlib import Path

from passlib.hash import sha512_crypt

from lib.config_loader import (
    ConfigError,
    _build_config,
    _load_toml_file,
    _load_toml_with_includes,
    _resolve_config_override,
    load_config,
)

from typing import Any

REPO_ROOT = Path(__file__).parents[2]


def _full_config_dict() -> dict[str, Any]:
    return load_config().model_dump()


def _config_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "lib.config_loader" and record.levelno >= logging.WARNING
    ]


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


def test_unknown_section_warned(caplog: pytest.LogCaptureFixture) -> None:
    data = _full_config_dict()
    data["not_a_section"] = 1
    with caplog.at_level(logging.WARNING, logger="lib.config_loader"):
        _build_config(data)
    assert any("not_a_section" in m for m in _config_warnings(caplog))


def test_unknown_key_warned(caplog: pytest.LogCaptureFixture) -> None:
    data = _full_config_dict()
    data["host"]["defalt_password"] = "typo"
    with caplog.at_level(logging.WARNING, logger="lib.config_loader"):
        _build_config(data)
    assert any("defalt_password" in m for m in _config_warnings(caplog))


def test_unknown_host_override_key_warned(caplog: pytest.LogCaptureFixture) -> None:
    data = _full_config_dict()
    data["hosts"]["1.2.3.4"] = {"pasword": "typo"}
    with caplog.at_level(logging.WARNING, logger="lib.config_loader"):
        _build_config(data)
    assert any("pasword" in m for m in _config_warnings(caplog))


def test_unknown_storage_key_warned(caplog: pytest.LogCaptureFixture) -> None:
    data = _full_config_dict()
    data["storage"]["lvmoiscsi"]["targetIQN"] = "ok"
    data["storage"]["lvmoiscsi"]["SCSIid"] = "ok"
    data["storage"]["lvmoiscsi"]["targetiqn"] = "typo"
    with caplog.at_level(logging.WARNING, logger="lib.config_loader"):
        _build_config(data)
    assert any("targetiqn" in m for m in _config_warnings(caplog))


def test_iso_alias_keys_not_warned(caplog: pytest.LogCaptureFixture) -> None:
    data = _full_config_dict()
    data["install"]["isos"]["definitions"]["83net"] = {"path": "x.iso", "net-url": "http://pxe/installers/xcp-ng/8.3"}
    with caplog.at_level(logging.WARNING, logger="lib.config_loader"):
        _build_config(data)
    assert _config_warnings(caplog) == []


def test_answerfiles_extra_keys_not_warned(caplog: pytest.LogCaptureFixture) -> None:
    data = _full_config_dict()
    data["install"]["answerfiles"]["INSTALL"]["mode"] = "upgrade"
    with caplog.at_level(logging.WARNING, logger="lib.config_loader"):
        _build_config(data)
    assert _config_warnings(caplog) == []


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


def test_inventory_from_config_empty_list_override() -> None:
    from lib.tools.inventory import inventory_from_config
    data = _full_config_dict()
    data["tools"]["update"] = {"repositories": ["xcp-ng-base"], "disabled_repositories": ["epel"]}
    data["hosts"] = {
        "h1": {"repositories": [], "disabled_repositories": ["*"]},
        "h2": {"repositories": ["xcp-ng-updates"]},
    }
    inv = inventory_from_config(_build_config(data))
    assert inv["hosts"]["h1"]["repositories"] == []
    assert inv["hosts"]["h1"]["disabled_repositories"] == ["*"]
    assert inv["hosts"]["h2"]["repositories"] == ["xcp-ng-updates"]
    assert inv["hosts"]["h2"]["disabled_repositories"] == ["epel"]


def test_resolve_config_override_as_path(tmp_path: Path) -> None:
    f = tmp_path / "my.toml"
    f.write_text("")
    assert _resolve_config_override(str(f)) == f.resolve()


def test_resolve_config_override_short_name_in_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.prod.toml").write_text("")
    monkeypatch.setenv("XCPNG_CONFIG_DIR", str(cfg_dir))
    assert _resolve_config_override("prod") == (cfg_dir / "config.prod.toml").resolve()


def test_resolve_config_override_short_name_in_repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lib.config_loader.REPO_ROOT", tmp_path)
    monkeypatch.setenv("XCPNG_CONFIG_DIR", str(tmp_path / "empty"))
    (tmp_path / "config.local.toml").write_text("")
    assert _resolve_config_override("local") == (tmp_path / "config.local.toml").resolve()


def test_dump_config_uses_aliases() -> None:
    data = _full_config_dict()
    data["install"]["isos"]["definitions"]["83net"] = {"path": "x.iso", "net-url": "http://pxe/installers/xcp-ng/8.3"}
    defn = _build_config(data).model_dump(by_alias=True)["install"]["isos"]["definitions"]["83net"]
    assert "net-url" in defn and defn["net-url"] == "http://pxe/installers/xcp-ng/8.3"
    assert "net_url" not in defn


def test_config_value_dotted_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('XCPNG_TESTS_host__default_password', '"envpass"')
    cfg = load_config(config_values=['host.default_password=clival'])
    assert cfg.host.default_password == "clival"
    assert sha512_crypt.verify("clival", cfg.host.default_password_hash)


def test_config_value_quoted_segment() -> None:
    cfg = load_config(config_values=['hosts."10.30.0.56".user=root'])
    assert cfg.hosts["10.30.0.56"].user == "root"


def test_config_value_parses_toml() -> None:
    cfg = load_config(config_values=['network.free_nics=["eth1","eth2"]'])
    assert cfg.network.free_nics == ["eth1", "eth2"]


def test_config_value_invalid_key() -> None:
    with pytest.raises(ConfigError):
        load_config(config_values=["no_equals_sign"])
