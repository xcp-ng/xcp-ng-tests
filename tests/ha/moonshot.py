"""Moonshot chassis power-on (REST /rest/v1/SystemsSummary).

Config: cp tests/ha/data.py-dist tests/ha/data.py
CARTRIDGES: --hosts address -> cartridge slot (int), e.g. 28 -> C28N1.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

from lib.common import wait_for
from tests.ha.net import address_is_reachable


class MoonshotConfigError(Exception):
    pass


def _system_id(slot: int) -> str:
    return f'C{slot}N1'


def _load_config() -> dict[str, Any]:
    try:
        from tests.ha import data as conf  # type: ignore[attr-defined]
    except ImportError as exc:
        raise MoonshotConfigError(
            'Missing tests/ha/data.py. Copy tests/ha/data.py-dist to tests/ha/data.py '
            'and set chassis credentials and CARTRIDGES mapping'
        ) from exc

    chassis_ip = os.environ.get('HPILO_HOST') or getattr(conf, 'CHASSIS_IP', None)
    username = os.environ.get('HPILO_USER') or getattr(conf, 'USERNAME', None)
    password = os.environ.get('HPILO_PASS') or getattr(conf, 'PASSWORD', None)
    cartridges = getattr(conf, 'CARTRIDGES', None)

    missing = [
        name
        for name, value in (
            ('CHASSIS_IP', chassis_ip),
            ('USERNAME', username),
            ('PASSWORD', password),
            ('CARTRIDGES', cartridges),
        )
        if not value and value != 0
    ]
    if missing:
        raise MoonshotConfigError(f'tests/ha/data.py is incomplete; set: {", ".join(missing)}')
    if not isinstance(cartridges, dict) or not cartridges:
        raise MoonshotConfigError('CARTRIDGES must be a non-empty dict of host address -> slot int')

    slots: dict[str, int] = {}
    for host_address, slot in cartridges.items():
        try:
            slots[str(host_address)] = int(slot)
        except (TypeError, ValueError) as exc:
            raise MoonshotConfigError(
                f'CARTRIDGES[{host_address!r}] must be an int slot, got {slot!r}'
            ) from exc

    return {
        'chassis_ip': chassis_ip,
        'username': username,
        'password': password,
        'cartridges': slots,
    }


class MoonshotClient:
    def __init__(self, chassis_ip: str, username: str, password: str, *, timeout_secs: float = 30):
        self.timeout_secs = timeout_secs
        self.session = requests.Session()
        self.session.verify = False
        self.session.auth = (username, password)
        self.base_url = f'https://{chassis_ip}'

    def _systems(self) -> list[dict[str, Any]]:
        systems = self.session.get(
            f'{self.base_url}/rest/v1/SystemsSummary',
            timeout=self.timeout_secs,
        )
        systems.raise_for_status()
        return [
            s for s in systems.json().get('Systems', [])
            if s.get('Name') != 'Cartridge 0 Node 0'
        ]

    def system_status(self, slot: int) -> dict[str, Any]:
        system_id = _system_id(slot)
        for system in self._systems():
            name = system.get('Name') or ''
            parts = name.split()
            # Name like "Cartridge 28 Node 1"
            if len(parts) >= 4 and f'C{parts[1]}N{parts[3]}' == system_id:
                return {
                    'name': name,
                    'system_id': system_id,
                    'power': system.get('Power') or system.get('PowerState'),
                }
        raise ValueError(f'Cartridge slot {slot} ({system_id}) not found on chassis')

    def power_on(self, slot: int) -> None:
        info = self.system_status(slot)
        logging.info('Moonshot power-on %s (%s)', info['name'], info['system_id'])
        response = self.session.post(
            f'{self.base_url}/rest/v1/SystemsSummary',
            json={
                'Action': 'Reset',
                'ResetType': 'On',
                'Targets': info['system_id'],
            },
            timeout=self.timeout_secs,
        )
        response.raise_for_status()
        messages = response.json().get('Messages', [])
        if messages:
            logging.info('Moonshot: %s', messages[0].get('MessageArgs', [''])[0])


def _slot_for_host(host_address: str, cartridges: dict[str, int]) -> int:
    if host_address not in cartridges:
        known = ', '.join(sorted(cartridges)) or '(none)'
        raise MoonshotConfigError(
            f'Host {host_address!r} not in CARTRIDGES; known: {known}. '
            'Update tests/ha/data.py so --hosts addresses match the map keys.'
        )
    return cartridges[host_address]


def power_on_host(
    host_address: str,
    *,
    wait_off_timeout_secs: int = 3 * 60,
    poll_delay_secs: int = 5,
) -> None:
    conf = _load_config()
    client = MoonshotClient(conf['chassis_ip'], conf['username'], conf['password'])
    slot = _slot_for_host(host_address, conf['cartridges'])

    def ready() -> bool:
        info = client.system_status(slot)
        power = str(info['power'] or '').strip().lower()
        logging.info('Moonshot %s (%s): Power=%s', info['name'], info['system_id'], info['power'])
        if power == 'on':
            if address_is_reachable(host_address):
                logging.info('Host already up, skip power-on')
                return True
            logging.info('Waiting for Moonshot Off before power-on')
            return False
        if power == 'off':
            logging.info('Powering on')
            client.power_on(slot)
            return True
        return False

    wait_for(
        ready,
        'Wait for Moonshot Off (or already up) then power-on',
        timeout_secs=wait_off_timeout_secs,
        retry_delay_secs=poll_delay_secs,
    )
