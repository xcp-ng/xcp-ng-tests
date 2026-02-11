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

while getopts ":f:t:" o; do
	case "${o}" in
		f)
			FEATURES=${OPTARG}
			show_by_features="yes"
			;;

		t)
			TEST=${OPTARG}
			;;
		*)
			usage
			;;
	esac
done
shift $((OPTIND-1))

if [ "${TEST}" != "" ]
then
	uv run pytest --collect-only --verbose -k ${TEST} | sed "/\s*<Function ${TEST}[\[>]/,\$!d" | sed "/\s*<Function ${TEST}[\[>]/,/\s*<Function/!d" | sed "/\s*<Function ${TEST}[^\[>]/d" | grep -v "====="
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
#	echo "Test ${test}:"
	_test=$(echo $test | sed 's@\[@\\\[@g; s@\]@\\\]@g')
	features=$(sed '/<Function '"${_test}"'/,/<Function/!d; /<Function '"${_test}"'/,/features: /!d; /features:/!d; s/\s*features://g' ${TMP})
	for searched_feature in ${FEATURES}
	do
		for feature in ${features}
		do
			if [[ "${feature}" == "${searched_feature}" ]]
			then
				echo ${test} | sed 's/\[.*\]//g'
				break 2
			fi
		done
	done
done

#rm ${TMP}
