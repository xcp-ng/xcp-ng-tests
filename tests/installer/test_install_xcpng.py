import logging
import pytest
import subprocess
from .conftest import check_vm, matches_rule
from lib.commands import ssh, SSHCommandFailed
from lib.pool import Pool

class Test:

    def test_check_install(self, host):

        # We're rebooting the host in case it's a fresh install. A reboot is mandatory to really have all services running.
        # This reboot help us to validate that xapi is responding.
        # host.reboot(verify=True)

        errors = False

        # check install-log
        log_installer_file = host.ssh(
            ['grep', '-ri', '\"\\] ran \\[\"', '/var/log/installer/install-log', '|', 'grep', '-v', '\"; rc 0\"']
            ).splitlines()
        # Need to clean the beginning of the line than we can compare it.
        # for element in log_installer_file:
        #     logging.info(element)

        # TODO: references fo all versions
        # 8.2.1
        expected_nonzero_rc_command_logs = [
        {
            'loglevel': 'INFO',
            'substrings': ["['blkid', '-s', 'PTTYPE', '-o', 'value', '/dev/"],
            'rc': 2
        },
        {
            'loglevel': 'INFO',
            'substrings': ["['/sbin/e2label', '/dev/sda5']"],
            'rc': 1
        }
        ]

        install_log_errors = []
        for error_line in log_installer_file:
            ignore_error = False
            for rule in expected_nonzero_rc_command_logs:
                if matches_rule(error_line, rule):
                    ignore_error = True
                    break
            if not ignore_error: 
                install_log_errors.append(error_line)

        # TODO: Add an logs annalyze on "YUM:   Installing :" in install-log
        # parse log fileand if the beginning of the line isn't "yum installing", Add lines in the array until the following "yum installing"

        # we're checking if somes services are in failed state.
        services_failed = host.ssh(['systemctl', '|', 'grep', '-i', '\"failed\"'], check=False).splitlines()

        # for set comparison, what's the best, just difference and error, or check each side to have a precise return ?

        # check our services are in the references list of services
        # active service
        active_services = host.ssh(
            ['systemctl', 'list-units', '--type=service', '--all',  '|', 'grep', '-i', '\"loaded    active\"']
            ).splitlines()
        se_active_services = set(active_services)
        with open('./tests/installer/active_services_821.txt', 'r') as f:
            references_services = f.readlines()
        se_references_services = set(references_services)
        # comparison_active = references_services - active_services
        comparison_active =  se_references_services.difference(se_active_services)

        # inactive and not defined services
        inactive_services = host.ssh(
            ['systemctl', 'list-units', '--type=service', '--all',  '|', 'grep', '-i', '\"inactive\"']
            ).splitlines()
        se_inactive_services = set(inactive_services)
        with open('./tests/installer/inactive_services_821.txt', 'r') as f:
            references_services = f.readlines()
        se_references_services = set(references_services)
        # comparison_inactive = references_services - inactive_services
        comparison_inactive = se_references_services.difference(se_inactive_services)

        if len(install_log_errors) > 0:
            errors = True
            for line in install_log_errors:
                logging.info(line)

        if len(services_failed) > 0:
            errors = True
            for line in services_failed:
                logging.info(line)

        if len(comparison_active) > 0:
            errors = True
            logging.info('There is some difference during the actives services comparison:')
            for line in comparison_active:
                logging.info(line)

        if len(comparison_inactive) > 0:
            errors = True
            logging.info('There is some difference during the inactives or not defined services comparison:')
            for line in comparison_inactive:
                logging.info(line)

        # if errors:
        #     raise Exception("Some unexpected errors was encountered. you can consult them above.")

# TODO :
# * produce a report on the state of the system
# * check installation logs `/var/log/installer/`. Detect errors. Ignore expected error messages. See for example https://gitlab.com/xcp-ng/dev-docs/-/wikis/check-installation-logs
# * check systemd services
#     * Are the expected services running ? Are some failed? `systemctl | grep -i failed` (I think)
#     * Run `systemd-analyze verify default.target`.
#     * Compare the list of running services after an additional reboot with a reference (produced from a working 8.2.1). The additional reboot is important because there are services that only run at first boot. (or produce the reference list from an XCP-ng that was just installed)
# * make sure check xapi answers requests (using `xe`)
# * [search logs (XAPI, deamon.log, dmesg...) for errors] => we'll do this later
# * make the test fail if we think there are bad things in the report
