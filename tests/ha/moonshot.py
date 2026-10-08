from __future__ import annotations

import logging
import warnings

import requests
import urllib3

from lib.common import wait_for
from lib.host import Host, host_data

POWER_ON_TIMEOUT_SECS = 3 * 60
# A powered-on host without SSH may just be rebooting after an HA fence.
BOOT_GRACE_SECS = 8 * 60
POLL_DELAY_SECS = 10
REQUEST_TIMEOUT_SECS = 30


class Moonshot:
    """Cartridge of an HPE Moonshot chassis, configured by HOSTS[..]['power_control'] in data.py."""

    def __init__(self, host_address: str):
        config = host_data(host_address).get('power_control') or {}
        if config.get('type') != 'moonshot':
            raise Exception(f'{host_address}: data.py needs HOSTS[..]["power_control"] with type "moonshot"')
        self.base_url = f'https://{config["chassis"]}/rest/v1'
        self.system_id = f'C{config["slot"]}N1'
        self.session = requests.Session()
        self.session.verify = False
        self.session.trust_env = False
        self.session.auth = (config['user'], config['password'])

    def _request(self, method: str, path: str, json: dict[str, str] | None = None) -> requests.Response:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', urllib3.exceptions.InsecureRequestWarning)
            response = self.session.request(
                method, f'{self.base_url}{path}', json=json, timeout=REQUEST_TIMEOUT_SECS
            )
        response.raise_for_status()
        return response

    def power(self) -> str:
        """Return 'on', 'off', or '' when the chassis does not answer."""
        try:
            systems = self._request('GET', '/SystemsSummary').json().get('Systems', [])
        except requests.RequestException as exc:
            logging.info(f'Moonshot status failed: {exc}')
            return ''
        for system in systems:
            # The 2nd and 4th words of the name are the cartridge and node numbers.
            words = system.get('Name', '').split()
            if len(words) >= 4 and f'C{words[1]}N{words[3]}' == self.system_id:
                power = str(system.get('Power') or system.get('PowerState') or '').strip().lower()
                logging.info(f'Moonshot {self.system_id}: Power={power}')
                return power
        raise Exception(f'Cartridge {self.system_id} not found on chassis')

    def reset(self, reset_type: str) -> None:
        logging.info(f'Moonshot {reset_type} {self.system_id}')
        self._request('POST', f'/Systems/{self.system_id}', json={'Action': 'Reset', 'ResetType': reset_type})


def power_on_host(host_address: str) -> None:
    """Power on the host, or cold-reset it if it is stuck. Returns once Power=On or SSH is up."""
    if Host.ssh_reachable(host_address):
        logging.info(f'Host {host_address} already up, skip power-on')
        return

    moonshot = Moonshot(host_address)
    with moonshot.session:
        if moonshot.power() == 'on':
            try:
                wait_for(
                    lambda: Host.ssh_reachable(host_address),
                    f'Wait for {host_address} to finish booting',
                    timeout_secs=BOOT_GRACE_SECS,
                    retry_delay_secs=POLL_DELAY_SECS,
                )
                return
            except TimeoutError:
                logging.warning(f'{host_address} powered on without SSH for {BOOT_GRACE_SECS}s: cold reset')
                moonshot.reset('ColdReset')

        def powered_on() -> bool:
            if Host.ssh_reachable(host_address) or moonshot.power() == 'on':
                return True
            try:
                moonshot.reset('On')
            except requests.RequestException as exc:
                logging.info(f'Moonshot power-on failed, retrying: {exc}')
            return False

        wait_for(
            powered_on,
            f'Wait for {host_address} to be powered on',
            timeout_secs=POWER_ON_TIMEOUT_SECS,
            retry_delay_secs=POLL_DELAY_SECS,
        )
