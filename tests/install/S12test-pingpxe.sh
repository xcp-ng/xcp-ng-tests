#!/bin/sh
case "$1" in
  start)
    sh -c 'while ! /usr/local/sbin/test-pingpxe.sh "{ARP_SERVER}"; do sleep 1 ; done' & ;;
  stop) ;;
esac
