#!/bin/bash -eu

#tests:
# for -t
# ./scripts/list.sh -t test_check_tools
# ./scripts/list.sh -t test_check_tools_after_reboot
# ./scripts/list.sh -t test_cross_pool_migration
# for listing all tests
# ./scripts/list.sh
# for listing tests testings specific features:
# ./scripts/list.sh -f migration

#set -x

show_by_features="no"
FEATURES=""
TEST=""
CHECK_MISSING="no"

usage() {
	echo "Usage: list.sh [-m] [-f <FEATURE_LIST>] [-t <TEST_NAME>]"
	echo
	echo "Options:"
	echo -e "\t-m\t\t\tPrint all tests that are missing docstring metadata"
	echo -e "\t-f <FEATURE_LIST>\tLists all tests that are testing a feature from the supplied FEATURE_LIST."
	echo -e "\t-t <TEST_NAME>\t\tShow docstring metadata of TEST_NAME: a description of what the test does"
	echo -e "\t\t\t\tand the list of tested features."
	echo
	echo "Examples:"
	echo 'List all tests that are testing the `migration` feature:'
	echo -e "\tlist.sh -f migration"
	echo 'List all tests that are testing `migration` or `cross-pool-migration` feature:'
	echo -e "\tlist.sh -f \"migration cross-pool-migration\""
	echo 'Show what the `test_cross_pool_migration` test is testing:'
	echo -e "\tlist.sh -t test_cross_pool_migration"

}

while getopts ":f:t:m" o; do
	case "${o}" in
		f)
			FEATURES=${OPTARG}
			show_by_features="yes"
			;;

		t)
			TEST=${OPTARG}
			;;
		m)
			CHECK_MISSING="yes"
			;;
		*)
			usage
			exit 1
			;;
	esac
done
shift $((OPTIND-1))

if [ "${CHECK_MISSING}" == "yes" ]
then
	TMP=$(mktemp)
	uv run pytest --collect-only --verbose > ${TMP}
	tests=$(sed '/\s<Function/!d; s/\s*<Function \(.*\)>/\1/g' ${TMP})
	echo Checking
	for test in ${tests}
	do
		_test=$(printf %q "${test}")
	done
	rm ${TMP}
	exit
fi

if [ "${TEST}" != "" ]
then
	uv run pytest --collect-only --verbose -k "${TEST}" | sed "/\s*<Function ${TEST}[\[>]/,\$!d" | sed '/^$/,/====/d' | sed "/\s*<Function ${TEST}[\[>]/,/\s*<Function/!d" | sed "/\s*<Function ${TEST}[^\[>]/d" | grep -v "====="
	exit
fi

if [ "${show_by_features}" == "no" ]
then
	uv run pytest --collect-only --verbose | grep -v '<Class ' \
		| grep -v '<Module ' | grep -v '<Package ' \
		| grep -v '<Dir ' \
		| sed 's/\s*<Function \(.*\)>/\1/g'
	exit
fi

TMP=$(mktemp)
uv run pytest --collect-only --verbose > ${TMP}
tests=$(sed '/\s<Function/!d; s/\s*<Function \(.*\)>/\1/g' ${TMP})

for test in ${tests}
do
	_test=$(printf %q "${test}")
	features=$(sed '/<Function '"${_test}"'/,/<Function/!d; /<Function '"${_test}"'/,/features: /!d; /features:/!d; s/\s*features://g' ${TMP})
	for searched_feature in ${FEATURES}
	do
		for feature in ${features}
		do
			if [[ "${feature}" == "${searched_feature}" ]]
			then
				echo "${test//\[*\]/}"
				break 2
			fi
		done
	done
done

rm ${TMP}
