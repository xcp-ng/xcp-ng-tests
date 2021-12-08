import logging

# Requirements:
# From --host parameter:
# - A XCP-ng host.
#
# /!\ Very long to execute

def test_quicktest(host):
    logging.info("Launching tests")
    host.ssh(['/opt/xensource/debug/quicktest'])
