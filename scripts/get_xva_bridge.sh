#!/bin/bash

set -u

# extract and read ova.xml
get_bridge()
{
    TMPFOLD=$(mktemp -d /tmp/xvaXXXX)
    tar -xf "${XVA_PATH}/${XVA_NAME}" -C "${TMPFOLD}" ova.xml
    chmod +r "${TMPFOLD}/ova.xml"
    XML_VALUE=$(grep -oE "<member><name>bridge</name><value>[^<]*</value></member>" "${TMPFOLD}/ova.xml")
    LENGTH=${#XML_VALUE}
    PREFIX_LENGTH=$((${LENGTH}-17))
    NETWORK_VALUE=$(cut -c 1-${PREFIX_LENGTH} <<< ${XML_VALUE})
    echo $(cut -c 35-${#NETWORK_VALUE} <<< ${NETWORK_VALUE})

    if [ -d "${TMPFOLD}" ]; then
        rm -Rf "${TMPFOLD}"
    fi
}

if [ ! -z "${1+set}" ]; then
    XVA_NAME=$(basename "$1")
    XVA_PATH=$(dirname "$1")
    if [ ! -e "$1" ]; then
        echo "File $1 doesn't exist. Check the name or the path."
        exit 1
    fi
else
    echo "Error: you forgot to specify the name of the file to scan"
    echo "Usage: $0 XVA_FILE"
    exit 1
fi

BRIDGE=$(get_bridge)
echo "${XVA_NAME}'s XAPI bridge network is: ${BRIDGE} and its compression method is: $(file ${XVA_PATH}/${XVA_NAME} | cut -f 2 -d :  | cut -f 2 -d " ")."
