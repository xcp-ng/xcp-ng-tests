#! /bin/bash
set -eE
set -o pipefail

ether_of () {
    ifconfig "$1" | grep ether | sed 's/.*ether \\([^ ]*\\).*/\\1/'
}

# on installed system, avoid xapi-project/xen-api#5799
if ! [ -e /opt/xensource/installer ]; then
    eth_mac=$(ether_of eth0)
    br_mac=$(ether_of xenbr0)

    # wait for bridge MAC to be fixed
    test "$eth_mac" = "$br_mac"
fi

if [ "$(readlink /bin/ping)" = busybox ]; then
    # XS before 7.0
    PINGARGS=""
else
    PINGARGS="-c1"
fi

ping $PINGARGS "$1"
