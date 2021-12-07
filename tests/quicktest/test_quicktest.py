import logging

# Requirements:
# From --host parameter:
# - A XCP-ng host.
#
# /!\ Very long to execute

def test_quicktest(host):
    logging.info("Launching tests")
    res = host.ssh(['/opt/xensource/debug/quicktest'])
    logging.debug("Test result: %s" % res)
