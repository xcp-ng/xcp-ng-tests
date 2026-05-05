#!/bin/bash

# SSH Public Key Authentication Matrix Test
#
# This script tests that older OpenSSH client versions can still connect to an
# XCP-ng server using public key authentication
#
# Usage:
#   test-ssh-public-key-auth.sh <host>
#
# Context:
# Due to the removal of the ssh-rsa signature algorithm in OpenSSH 9.8, older
# clients (version < 7.2) are no longer able to use public key authentication
# with ssh-rsa keys. Howerver, newer OpenSSH versions (7.2 and above) should
# still be able to authenticate using ssh-rsa keys, as well as ed25519 keys.
#
# This script can be used to verify this assumption by running a matrix of tests
# with different OpenSSH client versions and key types against a specified host.
# In practice, it should be run manually against a test XCP-ng server whenever
# the OpenSSH RPM package is updated, to ensure that we maintain compatibility
# with older clients.
#
# What does each test do?
# 1. Pull the specified OpenSSH client Docker image.
# 2. Generate a new SSH key pair using the OpenSSH client in the Docker container.
# 3. Copy the public key to the target host using ssh-copy-id with password
#    authentication.
# 4. Attempt to SSH into the host using the private key and run a simple command
#    to verify that authentication works.
# 5. Clean up by removing the public key from the host's authorized_keys file
#    and deleting the generated key pair.
#
# It requires Docker or Podman to be installed.

# -e: Exit immediately if a command exits with a non-zero status.
# -u: Treat unset variables as an error and exit immediately.
set -eu

# Constants and defaults
USER="root"
PASSWORD=""
VERSIONS=("7.2_p2-r5" "7.9_p1-r6" "8.8_p1-r1" "9.9_p2-r0" "10.2_p1-r0")
KEY_TYPES=("rsa" "ed25519")
SSH_OPTIONS="-o StrictHostKeyChecking=no"
IMAGE_BASE_NAME="sig9/alpine-openssh-client"

print_help() {
    cat <<'EOF'
Usage:
    test-ssh-public-key-auth.sh <host>

Description:
    Runs an SSH public key authentication matrix test with:
    - OpenSSH client versions: 7.2_p2-r5, 7.9_p1-r6, 8.8_p1-r1, 9.9_p2-r0, 10.2_p1-r0
    - Key types: rsa, ed25519
    Requires Docker or Podman to be installed.
EOF
}

silent_unless_fail() {
    local err
    # Run the command passed as arguments, capture stderr, discard stdout
    if ! err=$("$@" 2>/dev/stdout >/dev/null); then
        echo -e "\b\b\b: ❌\n$err" >&2
        return 1
    fi
}

status() {
    # Rewrite a single terminal status line for step-by-step progress.
    printf "\r\033[K$CURRENT_PREFIX %s" "$1"
}

build_prefix() {
    local host="$1"
    local version="$2"
    local key_type="$3"
    local version_fixed
    local key_type_fixed

    C_RESET=$'\e[0m'
    C_DIM=$'\e[2m'
    C_HOST_LABEL=$'\e[36m'
    C_HOST_VALUE=$'\e[1;96m'
    C_VER_LABEL=$'\e[33m'
    C_VER_VALUE=$'\e[1;93m'
    C_KEY_LABEL=$'\e[32m'
    C_KEY_VALUE=$'\e[1;92m'

    printf -v version_fixed "%-10.10s" "$version"
    printf -v key_type_fixed "%-7.7s" "$key_type"

    local prefix="${C_DIM}[${C_RESET}"
    prefix+="${C_HOST_LABEL}host${C_RESET}:${C_HOST_VALUE}${host}${C_RESET} "
    prefix+="${C_DIM}|${C_RESET} "
    prefix+="${C_VER_LABEL}ver${C_RESET}:${C_VER_VALUE}${version_fixed}${C_RESET} "
    prefix+="${C_DIM}|${C_RESET} "
    prefix+="${C_KEY_LABEL}key${C_RESET}:${C_KEY_VALUE}${key_type_fixed}${C_RESET}"
    prefix+="${C_DIM}]${C_RESET}"
    printf "%s" "$prefix"
}

run_case() {
    local version="$1"
    local key_type="$2"

    local image="$IMAGE_BASE_NAME:$version"
    local key_name="regression_testing_${version}_${key_type}"
    local comment="$(uuidgen)@testing-${version}-${key_type}"
    local cleanup="sed -i /$comment\$/d ~/.ssh/authorized_keys"

    CURRENT_PREFIX="$(build_prefix "$HOST" "$version" "$key_type")"

    status "Pulling Docker image $image..."
    if ! silent_unless_fail $CONTAINER_ENGINE pull "$image"; then
        return 1
    fi

    status "Testing SSH connection to $USER@$HOST using $image"
    rm -f "$key_name" "$key_name.pub"

    status "Generating SSH key pair..."
    if ! silent_unless_fail $CONTAINER_ENGINE run --rm -v "$PWD:/data" "$image" -c "cd /data && ssh-keygen -t $key_type -f $key_name -C '$comment' -N ''"; then
        rm -f "$key_name" "$key_name.pub"
        return 1
    fi

    status "Copying public key to $USER@$HOST..."
    if ! silent_unless_fail $CONTAINER_ENGINE run --rm -v "$PWD:/data" "$image" -c "mkdir -p /root/.ssh && sshpass -p '$PASSWORD' ssh-copy-id -i /data/$key_name.pub $SSH_OPTIONS '$USER@$HOST'"; then
        rm -f "$key_name" "$key_name.pub"
        return 1
    fi

    status "Verifying SSH connection..."
    if ! silent_unless_fail $CONTAINER_ENGINE run --rm -v "$PWD:/data" "$image" -c "ssh $SSH_OPTIONS -i /data/$key_name '$USER@$HOST' 'echo hello'"; then
        rm -f "$key_name" "$key_name.pub"
        return 1
    fi

    status "SSH connection successful, cleaning up..."
    if ! silent_unless_fail $CONTAINER_ENGINE run --rm -v "$PWD:/data" "$image" -c "ssh $SSH_OPTIONS -i /data/$key_name '$USER@$HOST' '$cleanup'"; then
        rm -f "$key_name" "$key_name.pub"
        return 1
    fi
    rm -f "$key_name" "$key_name.pub"

    status "PASS ✅"
    printf "\n"
    return 0
}


# Parse arguments
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    print_help
    exit 0
fi

if [ "$#" -ne 1 ]; then
    echo "Expected exactly one argument: <host>" >&2
    print_help
    exit 1
fi

HOST="$1"


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


# Run the matrix of tests
TOTAL=0
PASSED=0
FAILED=0

for VERSION in "${VERSIONS[@]}"; do
    for KEY_TYPE in "${KEY_TYPES[@]}"; do
        TOTAL=$((TOTAL + 1))
        if run_case "$VERSION" "$KEY_TYPE"; then
            PASSED=$((PASSED + 1))
        else
            FAILED=$((FAILED + 1))
        fi
    done
done

printf "\e[1mMatrix run complete\e[0m  total: %d  \e[32mpassed: %d\e[0m  \e[31mfailed: %d\e[0m\n" "$TOTAL" "$PASSED" "$FAILED"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
