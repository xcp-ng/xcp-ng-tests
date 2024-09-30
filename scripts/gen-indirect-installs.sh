#!/bin/bash
set -e

# creation of reference installations to be used for basis of upgrade tests

# * needs at least --hosts=$NEST
# * ch821 and xs8 needs ISO in the cache

. $(dirname $0)/lib.bash

TESTCLASS="tests/install/test.py::TestNested"
for conf in uefi-75+821.1-iso-ext; do
    IFS=- read fw versions media sr < <(echo "$conf")
    IFS=+ read origversion stepversion < <(echo "$versions")
    TESTS=(
        $TESTCLASS::test_install[$fw-$origversion-$media-$sr]
        $TESTCLASS::test_tune_firstboot[None-$fw-$origversion-host1-$media-$sr]
        $TESTCLASS::test_boot_inst[$fw-$origversion-host1-$media-$sr]

        $TESTCLASS::test_upgrade[$fw-$origversion-$stepversion-host1-$media-$sr]
        $TESTCLASS::test_boot_upg[$fw-$origversion-$stepversion-host1-$media-$sr]

        #$TESTCLASS::test_upgrade[$fw-$origversion-$stepversion-83nightly-host1-$media-$sr]
    )
    run_pytest "$conf" \
        --log-file=test-genref-$conf.log \
        --reruns=5 --only-rerun=TimeoutError \
        "$@" \
        "${TESTS[@]}"
done

report_failures
