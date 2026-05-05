#!/bin/bash

# SSH Public Key Authentication Test
#
# This script tests that older OpenSSH client versions can still connect to an
# XCP-ng server using public key authentication
#
# Usage:
#   test-ssh-public-key-auth.sh <host> <version> <keytype>
#
# Context:
# Due to the removal of the ssh-rsa signature algorithm in OpenSSH 9.8, older
# clients (version < 7.2) are no longer able to use public key authentication
# with ssh-rsa keys. Howerver, newer OpenSSH versions (7.2 and above) should
# still be able to authenticate using ssh-rsa keys, as well as ed25519 keys.
#
# This script can be used to verify this assumption by running:
#
#     $ scripts/test-ssh-public-key-auth.sh HOST 7.2_p2-r5 rsa
#
# It requires Docker or Podman to be installed.

# -e: Exit immediately if a command exits with a non-zero status.
# -u: Treat unset variables as an error and exit immediately.
set -eu

# Constants and defaults
USER="root"
PASSWORD=""
SSH_OPTIONS="-o StrictHostKeyChecking=no"
IMAGE_BASE_NAME="sig9/alpine-openssh-client"

print_help() {
    cat <<'EOF'
Usage:
    test-ssh-public-key-auth.sh <host> <version> <keytype>

Description:
    Runs an SSH public key authentication test
    Requires Docker or Podman to be installed.

Example:
    scripts/test-ssh-public-key-auth.sh HOST 7.2_p2-r5 rsa
EOF
}

silent_unless_fail() {
    local err
    # Run the command passed as arguments, capture stderr, discard stdout
    if ! err=$("$@" 2>/dev/stdout >/dev/null); then
        echo "$err" >&2
        echo "❌ Test failed"
        return 1
    fi
}

# Parse arguments
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    print_help
    exit 0
fi
if [ "$#" -ne 3 ]; then
    echo "Expected exactly three arguments: <host> <version> <keytype>" >&2
    print_help
    exit 1
fi

# Get arguments
HOST="$1"
VERSION="$2"
KEY_TYPE="$3"
IMAGE="$IMAGE_BASE_NAME:$VERSION"
KEY_NAME="testing_${VERSION}_${KEY_TYPE}"
COMMENT="$(uuidgen)@testing-${VERSION}-${KEY_TYPE}"
CLEANUP="sed -i /$COMMENT\$/d ~/.ssh/authorized_keys"

# Detect the container engine
if command -v podman &> /dev/null; then
    CONTAINER_ENGINE="podman"
elif command -v docker &> /dev/null; then
    CONTAINER_ENGINE="docker"
else
    echo "Error: Neither podman nor docker found."
    exit 1
fi

# Prompt for password
if [ -z "$PASSWORD" ]; then
    read -s -p $'Enter \e[1m'"$USER@$HOST"$'\e[0m password: ' PASSWORD
    echo ""
fi

echo "Pulling Docker image $IMAGE..."
silent_unless_fail $CONTAINER_ENGINE pull "$IMAGE"

echo "Generating SSH key pair..."
trap 'rm -f "$KEY_NAME" "$KEY_NAME.pub"' EXIT
silent_unless_fail $CONTAINER_ENGINE run --rm -v "$PWD:/data" "$IMAGE" -c "cd /data && ssh-keygen -t $KEY_TYPE -f $KEY_NAME -C '$COMMENT' -N ''"

echo "Copying public key to $USER@$HOST..."
silent_unless_fail $CONTAINER_ENGINE run --rm -v "$PWD:/data" "$IMAGE" -c "mkdir -p /root/.ssh && sshpass -p '$PASSWORD' ssh-copy-id -i /data/$KEY_NAME.pub $SSH_OPTIONS '$USER@$HOST'"

echo "Verifying SSH connection..."
silent_unless_fail $CONTAINER_ENGINE run --rm -v "$PWD:/data" "$IMAGE" -c "ssh $SSH_OPTIONS -i /data/$KEY_NAME '$USER@$HOST' 'echo hello'"

echo "SSH connection successful, cleaning up..."
silent_unless_fail $CONTAINER_ENGINE run --rm -v "$PWD:/data" "$IMAGE" -c "ssh $SSH_OPTIONS -i /data/$KEY_NAME '$USER@$HOST' '$CLEANUP'"

echo "✅ Test passed"
