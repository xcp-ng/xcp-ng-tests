#!/bin/sh
set -e

# Remove apk cache
rm -rf /var/cache/apk/*

# Remove log files
find /var/log -type f -exec truncate -s 0 {} \;

# Remove temporary files
rm -rf /tmp/* /var/tmp/*

# Clear machine-id so each clone gets a unique one
printf '' > /etc/machine-id 2>/dev/null || true

# Remove history
rm -f /root/.ash_history /root/.bash_history
