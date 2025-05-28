## shell library for scripts chaining tests

# error reporting

FAILURES=""

report_failures() {
    if [ -n "$FAILURES" ]; then
        echo >&2 "ERROR: tests failed: $FAILURES"
    else
        echo >&2 "All tests passed."
    fi
}

# pytest running

run_pytest() {
    local conf="$1"
    shift

    if [ "$1" = "--runner" ]; then
        local RUNNER="$2"
        shift 2
    else
        local RUNNER="pytest"
    fi

    local OPTS=(--log-file-level=DEBUG --show-capture=stdout --show-capture=stderr
          --maxfail=0)

    echo >&2 "Running: $RUNNER ${OPTS[@]} $@"
    if $RUNNER \
           "${OPTS[@]}" \
           "$@"
    then
        :
    else
        local ret="$?"
        case "$ret" in
            5) ;; # NO_TESTS_COLLECTED = not a problem
            *)
                FAILURES+=" $conf ($ret)" ;;
        esac
    fi
}

# reference configurations, to be upgraded to nightly

# FIXME this is also where we take the list to create all ref
# installs, 75/76 should be separated
REFVERSIONS=(
    830
    821.1 81 80
    76 75
    xs8
    ch821.1
)

REFCONFS=()
init_refconfs() {
    local version fw
    for version in "${REFVERSIONS[@]}"; do
        for fw in uefi bios; do
            REFCONFS+=($fw-$version-iso-ext)
        done
    done
}

init_refconfs

upgrade_should_work() {
    case "$1" in
        83b2)
            echo >&2 "SKIP: no upgrade: '$1' needs 'yum update' for 'setup'"
            return 1 ;;
        *) return 0 ;;
    esac
}

# configurations to be tested on nightly

TESTCONFS=()
init_testconfs() {
    local fw sr
    for fw in uefi bios; do
        for sr in ext lvm; do
            TESTCONFS+=($fw-$sr)
        done
    done
}

init_testconfs
