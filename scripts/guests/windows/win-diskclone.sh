#!/bin/bash
set -eu

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
                 grep -v type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 |
                 cut -d ':' -f 1)
do
    dstpart=${srcpart/"$src"/"$dst"}
    echo "Cloning $srcpart to $dstpart"
    pv $srcpart > $dstpart
done

echo "Cloning data partitions"
for srcpart in $(sfdisk -d $src |
                 grep type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 |
                 cut -d ':' -f 1)
do
    dstpart=${srcpart/"$src"/"$dst"}
    echo "Deleting pagefiles"
    mkdir -p /mnt/$srcpart &&
        mount $srcpart /mnt/$srcpart &&
        find /mnt/$srcpart -maxdepth 1 -iname pagefile.sys -or -iname hiberfil.sys -or -iname swapfile.sys -delete
    umount /mnt/$srcpart
    echo "Cloning $srcpart to $dstpart"
    ntfsclone -O $dstpart $srcpart
done
