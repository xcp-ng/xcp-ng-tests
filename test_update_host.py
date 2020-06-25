from lib.common import wait_for, wait_for_not

def test(host):
    print("Check for updates")
    if not host.has_updates():
        print("No updates available for the host. Stopping.")
        return

    host.install_updates()
    host.restart_toolstack()
    wait_for(host.is_enabled, "Wait for host enabled")
    host.reboot()
    wait_for_not(host.is_enabled, "Wait for host down")
    wait_for(host.is_enabled, "Wait for host up", timeout_secs=300)
    print("Check for updates again")
    assert(not host.has_updates())
