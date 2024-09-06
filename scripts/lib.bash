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
