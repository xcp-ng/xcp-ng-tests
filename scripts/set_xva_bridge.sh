#!/bin/bash

set -u

# functions
# usage
usage()
{
    echo "----------------------------------------------------------------------------------------"
    echo "- Usage: $0 [XVA_filename] compression[zstd|gzip] bridge_value[xenbr0|xapi[:9]|...]"
    echo "- All options are mandatory."
    echo "----------------------------------------------------------------------------------------"
}

# check parameters and prompt the usage if needed
if [ -z "${1+set}" ]
then
    echo "Error: XVA name missing."
    usage
    exit 1
else
    if [ -e "${1}" ]; then
        XVA_NAME=$(realpath "$1")

        if [ ${XVA_NAME##*.} !=  xva ]
        then
            echo "Error: ${XVA_NAME} doesn't seem to be a xva file"
            usage
            exit 1
        fi
    else
        echo "Error: file $1 not found."
        usage
        exit 1
    fi
fi

if ([ "$2" = "gzip" ] || [ "$2" = "zstd" ])
then
    COMPRESS_METHOD=$2
else
    echo "Error: unsupported compression value."
    usage
    exit 1
fi

if [ -z "${3+set}" ]
then
    echo "Error: please specify the new bridge value."
    usage
    exit 1
else
    BRIDGE_VALUE=$3
fi

# we detect the compression method of the xva to uncompress it right
OLD_COMPRESSION=$(file -b "${XVA_NAME}" | cut -f 1 -d " ")
if [ "${OLD_COMPRESSION}" != "Zstandard" ] && [ "${OLD_COMPRESSION}" != "gzip" ] && [ "${OLD_COMPRESSION}" != "tar" ]; then
            echo "Error: unknown compression type detected for ${XVA_NAME}: ${OLD_COMPRESSION}"
            exit 1
fi

PATHFOLDER=$(dirname "${XVA_NAME}")
TMPFOLDER=$(mktemp -d "${PATHFOLDER}"/xvaXXXX)

# extract and create the file list at the same time
TMP_LIST=$(mktemp "${PATHFOLDER}"/SortedListXXXX.txt)
if [ -f "${XVA_NAME}" ]; then
    tar -xvf "${XVA_NAME}" -C "${TMPFOLDER}" > "${TMP_LIST}"
else
    echo "Error: ${XVA_NAME} not found."
    exit 1
fi

chmod -R u+rX "${TMPFOLDER}"

if [ -e "${TMPFOLDER}/ova.xml" ]; then
    sed -i "s/<member><name>bridge<\/name><value>[^<]*<\/value><\/member>/<member><name>bridge<\/name><value>${BRIDGE_VALUE}<\/value><\/member>/g" "${TMPFOLDER}/ova.xml"
else
    echo "Error: File ova.xml not found during the sed."
    exit 1
fi


# save first file
mv "${XVA_NAME}" "${XVA_NAME}.save"

# Create the new XVA
tar -C "${TMPFOLDER}" --${COMPRESS_METHOD} -cf "${XVA_NAME}" --no-recursion -T "${TMP_LIST}" --numeric-owner --owner=:0 --group=:0 --mode=ugo= --mtime=@0
rm -f "${TMP_LIST}"

# clean TMPFOLDER
if [ -d "${TMPFOLDER}" ]; then
    rm -Rf "${TMPFOLDER}"
else
    echo "Warning: No tmp folder to delete."
fi
