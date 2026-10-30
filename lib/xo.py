import pytest

import json

from data import TOOLS
from lib.commands import local_cmd
from lib.typing import JSONType

from typing import Literal, overload

__allow_xo_cli = False
def _allow_xo_cli(value: bool) -> bool:
    """
    Permit to configure the usage of xo_cli function (returns the previous value).
    This function shouldn't be called directly.
    If you need xo_cli(), use the hosts_with_xo fixture.
    """
    global __allow_xo_cli

    old = __allow_xo_cli
    __allow_xo_cli = value

    return old

@overload
def xo_cli(action: str, args: dict[str, str] = ..., *, check: bool = ..., use_json: Literal[False] = ...) -> str:
    ...
@overload
def xo_cli(action: str, args: dict[str, str] = ..., *, check: bool = ..., use_json: Literal[True]) -> JSONType:
    ...

def xo_cli(action: str, args: dict[str, str] = {}, *, check: bool = True, use_json: bool = False) -> JSONType | str:
    if not __allow_xo_cli:
        pytest.fail("xo_cli function requires hosts_with_xo fixture usage.")

    cmd = [TOOLS.get('xo-cli', 'xo-cli'), action]
    if action != 'list-objects' and use_json:
        cmd += ['--json']
    cmd += ["%s=%s" % (key, value) for key, value in args.items()]

    res = local_cmd(cmd, check=check)

    if use_json:
        return json.loads(res.stdout)

    return res.stdout


def json_check_get_str(d: dict[str, JSONType], key: str, default: str | None = None) -> str:
    value = d.get(key, default)
    assert isinstance(value, str)
    return value

def json_check_get_bool(d: dict[str, JSONType], key: str, default: bool | None = None) -> bool:
    value = d.get(key, default)
    assert isinstance(value, bool)
    return value


class XoServer:
    id: str
    host: str
    status: str

    def __init__(self, json: JSONType) -> None:
        assert isinstance(json, dict)
        self.id = json_check_get_str(json, 'id')
        self.host = json_check_get_str(json, 'host')
        self.status = json_check_get_str(json, 'status')


def xo_servers(host: str | None = None) -> list[XoServer]:
    """
    Returns the registered servers in XO, possibly fitering by [host].
    """
    servers_json = xo_cli('server.getAll', use_json=True)
    assert isinstance(servers_json, list)

    servers = map(XoServer, servers_json)

    if host is not None:
        servers = filter(
            lambda s: s.host == host,
            servers,
        )

    return list(servers)

def xo_server_add(label: str, host: str, username: str, password: str, allowUnauthorized: bool) -> str:
    uuid = xo_cli('server.add', {
        'host': host,
        'username': username,
        'password': password,
        'allowUnauthorized': 'true' if allowUnauthorized else 'false',
        'label': label,
    },
        use_json=True,
    )
    assert isinstance(uuid, str)
    return uuid

def xo_server_remove(uuid: str) -> None:
    xo_cli('server.remove', {'id': uuid})


class XoPlugin:
    id: str
    loaded: bool
    version: str

    def __init__(self, json: JSONType) -> None:
        assert isinstance(json, dict)
        self.id = json_check_get_str(json, 'id')
        self.loaded = json_check_get_bool(json, 'loaded', False)
        self.version = json_check_get_str(json, 'version')

def xo_plugins(id: str | None = None) -> list[XoPlugin]:
    """
    Returns the plugins in XO, possibly filtering by [id].
    """
    plugins_json = xo_cli('plugin.get', use_json=True)
    assert isinstance(plugins_json, list)

    plugins = map(XoPlugin, plugins_json)

    if id is not None:
        plugins = filter(
            lambda s: s.id == id,
            plugins,
        )

    return list(plugins)


def xo_object_exists(uuid: str) -> bool:
    """
    Returns if an object with [uuid] exists.
    """
    lst = xo_cli('list-objects', {'uuid': uuid}, use_json=True)
    assert isinstance(lst, list)
    return len(lst) > 0
