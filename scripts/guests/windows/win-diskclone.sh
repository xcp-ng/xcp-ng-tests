#!/bin/bash
set -eu

win_diskclean() {
    mkdir -p "/run/win-diskclone/$1"
    mount $1 "/run/win-diskclone/$1"
    find "/run/win-diskclone/$1" -maxdepth 1 \( -iname pagefile.sys -or -iname hiberfil.sys -or -iname swapfile.sys \) -delete
    umount "/run/win-diskclone/$1"
}

src=$1
dst=$2

if [ "$(blockdev --getsz $src)" != "$(blockdev --getsz $dst)" ]
then
    echo "Disks are not the same size!"
    exit 1
fi

if (sfdisk -d $dst > /dev/null)
then
    echo "Destination contains partition table! Stopping"
    exit 1
fi

echo "Cloning partition table"
sfdisk -d $src | sfdisk $dst

echo "Cloning non-data partitions"
for srcpart in $(sfdisk -d $src |
                 grep start= |
                 grep -v 'type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7\|type=DE94BBA4-06D1-4D40-A16A-BFD50179D6AC' |
                 cut -d ':' -f 1)
do
    dstpart=${srcpart/"$src"/"$dst"}
    echo "Cloning $srcpart to $dstpart"
    pv $srcpart > $dstpart
done

echo "Cloning data partitions"
mkdir -p /run/win-diskclone
for srcpart in $(sfdisk -d $src |
                 grep 'type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7\|type=DE94BBA4-06D1-4D40-A16A-BFD50179D6AC' |
                 cut -d ':' -f 1)
do
    dstpart=${srcpart/"$src"/"$dst"}
    echo "Cleaning $srcpart"
    win_diskclean $srcpart
    echo "Cloning NTFS $srcpart to $dstpart"
    ntfsclone -O $dstpart $srcpart
done

sync
