#!/bin/sh
set -ex

ROOT="$1"

# Write new files
cat > /tmp/test-pingpxe.sh <<'EOF'
{TEST_PINGPXE_SH}
EOF
cat > /tmp/test-pingpxe.service <<'EOF'
{TEST_PINGPXE_SERVICE}
EOF
cat > /tmp/S12test-pingpxe <<'EOF'
{TEST_PINGPXE_S12}
EOF

# Add test-pingpxe script
mkdir -p "$ROOT/usr/local/sbin"
cp /tmp/test-pingpxe.sh "$ROOT/usr/local/sbin/test-pingpxe.sh"
chmod +x "$ROOT/usr/local/sbin/test-pingpxe.sh"

# Add systemd service
if [ -d "$ROOT/etc/systemd/system" ]; then
    cp /tmp/test-pingpxe.service "$ROOT/etc/systemd/system/test-pingpxe.service"
    systemctl --root="$ROOT" enable test-pingpxe.service
# Add sysv script
else
    cp /tmp/S12test-pingpxe "$ROOT/etc/init.d/test-pingpxe"
    chmod +x "$ROOT/etc/init.d/test-pingpxe"
    ln -s ../init.d/test-pingpxe "$ROOT/etc/rc3.d/S11test-pingpxe"
fi

# Add SSH CI keys
mkdir -p "$ROOT/root/.ssh"
echo "{TEST_SSH_PUBKEY}" >> "$ROOT/root/.ssh/authorized_keys"
