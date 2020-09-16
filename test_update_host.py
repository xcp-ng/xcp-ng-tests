from lib.common import wait_for, wait_for_not

def test(host):
    print("Check for updates")
    if not host.has_updates():
        print("No updates available for the host. Stopping.")
        return

    host.install_updates()
    host.restart_toolstack()
    wait_for(host.is_enabled, "Wait for host enabled")
    host.reboot(verify=True)
    print("Check for updates again")
    assert not host.has_updates()
