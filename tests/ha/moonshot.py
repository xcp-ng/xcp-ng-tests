from __future__ import annotations

import logging
import os
import warnings

import requests
import urllib3

from lib.common import wait_for
from tests.ha.net import address_is_reachable

def _moonshot_session():
    try:
        from tests.ha import data as conf  # type: ignore[attr-defined]
    except ImportError:
        raise Exception('Missing tests/ha/data.py (copy from data.py-dist)')

    chassis_ip = os.environ.get('HPILO_HOST') or getattr(conf, 'CHASSIS_IP', None)
    username = os.environ.get('HPILO_USER') or getattr(conf, 'USERNAME', None)
    password = os.environ.get('HPILO_PASS') or getattr(conf, 'PASSWORD', None)
    cartridges = getattr(conf, 'CARTRIDGES', None)
    if not chassis_ip or not username or not password:
        raise Exception('Set CHASSIS_IP, USERNAME, PASSWORD in tests/ha/data.py')
    if not isinstance(cartridges, dict) or not cartridges:
        raise Exception('CARTRIDGES must be a non-empty host -> slot dict')

    session = requests.Session()
    session.verify = False
    session.trust_env = False
    session.auth = (username, password)
    return session, f'https://{chassis_ip}', {str(addr): int(slot) for addr, slot in cartridges.items()}


def _request(session, base_url, method, path, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', urllib3.exceptions.InsecureRequestWarning)
        response = session.request(method, f'{base_url}{path}', timeout=30, **kwargs)
    response.raise_for_status()
    return response


def _system(session, base_url, slot):
    system_id = f'C{slot}N1'
    response = _request(session, base_url, 'GET', '/rest/v1/SystemsSummary')
    for system in response.json().get('Systems', []):
        name = system.get('Name') or ''
        parts = name.split()
        if len(parts) >= 4 and f'C{parts[1]}N{parts[3]}' == system_id:
            return name, system_id, system.get('Power') or system.get('PowerState')
    raise Exception(f'Cartridge {system_id} not found on chassis')


def _power(session, base_url, slot):
    try:
        name, system_id, power = _system(session, base_url, slot)
    except requests.RequestException as exc:
        logging.info('Moonshot status failed (retrying): %s', exc)
        return ''
    logging.info('Moonshot %s (%s): Power=%s', name, system_id, power)
    return str(power or '').strip().lower()


def _reset(session, base_url, slot, reset_type):
    name, system_id, _ = _system(session, base_url, slot)
    logging.info('Moonshot %s %s (%s)', reset_type, name, system_id)
    _request(
        session,
        base_url,
        'POST',
        f'/rest/v1/Systems/{system_id}',
        json={'Action': 'Reset', 'ResetType': reset_type},
    )


def power_on_host(host_address, *, timeout_secs=3 * 60, poll_delay_secs=10):
    if address_is_reachable(host_address):
        logging.info('Host %s already up, skip power-on', host_address)
        return

    session, base_url, cartridges = _moonshot_session()
    if host_address not in cartridges:
        known = ', '.join(sorted(cartridges)) or '(none)'
        raise Exception(f'Host {host_address!r} not in CARTRIDGES; known: {known}')
    slot = cartridges[host_address]

    def issue_reset():
        power = _power(session, base_url, slot)
        _reset(session, base_url, slot, 'ColdReset' if power == 'on' else 'On')

    try:
        issue_reset()
    except requests.RequestException as exc:
        logging.info('Moonshot reset failed (will retry): %s', exc)

    def powered_or_up():
        if address_is_reachable(host_address) or _power(session, base_url, slot) == 'on':
            return True
        try:
            issue_reset()
        except requests.RequestException as exc:
            logging.info('Moonshot reset failed (retrying): %s', exc)
        return False

    wait_for(
        powered_or_up,
        'Wait for Moonshot Power=On (or host up)',
        timeout_secs=timeout_secs,
        retry_delay_secs=poll_delay_secs,
    )
