from __future__ import annotations

# flake8: noqa: E501 - lsblk output lines are intentionally long
from unittest.mock import MagicMock

from lib.common import KiB
from lib.host import Host

# ---------------------------------------------------------------------------
# lsblk fixtures
# ---------------------------------------------------------------------------

# Full real-world output with multipath (multiple paths per device).
# Two mpath devices:
#   dm-0  (3600507681381022548000000000001ec)  - in use: mounted partitions
#   dm-8  (3600507638081046dd800000000000043)  - free: one lvm child (unused)
# Plain disks:
#   sdf / sdb / sdm / sde / sdc / sdj / sdr  - free (no children, no mpath)
#   sdd / sdk / sdg / sdh / sdl / sdp / sdq / sdt - paths for dm-0 (in use)
#   sdo / sds / sdi / sda                         - paths for dm-8 (free)
LSBLK_FULL = """\
NAME="sdf" KNAME="sdf" PKNAME="" SIZE="21990232555520" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810286"
NAME="sdo" KNAME="sdo" PKNAME="" SIZE="54975581388800" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x600507638081046d"
NAME="3600507638081046dd800000000000043" KNAME="dm-8" PKNAME="sdo" SIZE="54975581388800" LOG-SEC="512" TYPE="mpath" MOUNTPOINT="" WWN=""
NAME="VG_XenStorage--aed646f9--4764--81b7--9675--e79c19ce0a41-MGT" KNAME="dm-9" PKNAME="dm-8" SIZE="4194304" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="" WWN=""
NAME="sdd" KNAME="sdd" PKNAME="" SIZE="536870912000" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdd2" KNAME="sdd2" PKNAME="sdd" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdd5" KNAME="sdd5" PKNAME="sdd" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdd3" KNAME="sdd3" PKNAME="sdd" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdd1" KNAME="sdd1" PKNAME="sdd" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdd6" KNAME="sdd6" PKNAME="sdd" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdd4" KNAME="sdd4" PKNAME="sdd" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="3600507681381022548000000000001ec" KNAME="dm-0" PKNAME="sdd" SIZE="536870912000" LOG-SEC="512" TYPE="mpath" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec1" KNAME="dm-1" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="/" WWN=""
NAME="3600507681381022548000000000001ec6" KNAME="dm-6" PKNAME="dm-0" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="[SWAP]" WWN=""
NAME="3600507681381022548000000000001ec4" KNAME="dm-4" PKNAME="dm-0" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="/boot/efi" WWN=""
NAME="3600507681381022548000000000001ec2" KNAME="dm-2" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec5" KNAME="dm-5" PKNAME="dm-0" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="/var/log" WWN=""
NAME="3600507681381022548000000000001ec3" KNAME="dm-3" PKNAME="dm-0" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="XSLocalEXT--3816bde9--226b--5f15--640c--121ffa6bc20b-3816bde9--226b--5f15--640c--121ffa6bc20b" KNAME="dm-7" PKNAME="dm-3" SIZE="492293849088" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="/run/sr-mount/3816bde9-226b-5f15-640c-121ffa6bc20b" WWN=""
NAME="sdm" KNAME="sdm" PKNAME="" SIZE="21990232555520" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810286"
NAME="sdb" KNAME="sdb" PKNAME="" SIZE="21990232555520" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810286"
NAME="sdk" KNAME="sdk" PKNAME="" SIZE="536870912000" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdk5" KNAME="sdk5" PKNAME="sdk" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdk3" KNAME="sdk3" PKNAME="sdk" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdk1" KNAME="sdk1" PKNAME="sdk" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdk6" KNAME="sdk6" PKNAME="sdk" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdk4" KNAME="sdk4" PKNAME="sdk" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdk2" KNAME="sdk2" PKNAME="sdk" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="3600507681381022548000000000001ec" KNAME="dm-0" PKNAME="sdk" SIZE="536870912000" LOG-SEC="512" TYPE="mpath" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec1" KNAME="dm-1" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="/" WWN=""
NAME="3600507681381022548000000000001ec6" KNAME="dm-6" PKNAME="dm-0" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="[SWAP]" WWN=""
NAME="3600507681381022548000000000001ec4" KNAME="dm-4" PKNAME="dm-0" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="/boot/efi" WWN=""
NAME="3600507681381022548000000000001ec2" KNAME="dm-2" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec5" KNAME="dm-5" PKNAME="dm-0" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="/var/log" WWN=""
NAME="3600507681381022548000000000001ec3" KNAME="dm-3" PKNAME="dm-0" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="XSLocalEXT--3816bde9--226b--5f15--640c--121ffa6bc20b-3816bde9--226b--5f15--640c--121ffa6bc20b" KNAME="dm-7" PKNAME="dm-3" SIZE="492293849088" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="/run/sr-mount/3816bde9-226b-5f15-640c-121ffa6bc20b" WWN=""
NAME="sds" KNAME="sds" PKNAME="" SIZE="54975581388800" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x600507638081046d"
NAME="3600507638081046dd800000000000043" KNAME="dm-8" PKNAME="sds" SIZE="54975581388800" LOG-SEC="512" TYPE="mpath" MOUNTPOINT="" WWN=""
NAME="VG_XenStorage--aed646f9--4764--81b7--9675--e79c19ce0a41-MGT" KNAME="dm-9" PKNAME="dm-8" SIZE="4194304" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="" WWN=""
NAME="sdi" KNAME="sdi" PKNAME="" SIZE="54975581388800" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x600507638081046d"
NAME="3600507638081046dd800000000000043" KNAME="dm-8" PKNAME="sdi" SIZE="54975581388800" LOG-SEC="512" TYPE="mpath" MOUNTPOINT="" WWN=""
NAME="VG_XenStorage--aed646f9--4764--81b7--9675--e79c19ce0a41-MGT" KNAME="dm-9" PKNAME="dm-8" SIZE="4194304" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="" WWN=""
NAME="sdq" KNAME="sdq" PKNAME="" SIZE="536870912000" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdq4" KNAME="sdq4" PKNAME="sdq" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdq2" KNAME="sdq2" PKNAME="sdq" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdq5" KNAME="sdq5" PKNAME="sdq" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdq3" KNAME="sdq3" PKNAME="sdq" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdq1" KNAME="sdq1" PKNAME="sdq" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdq6" KNAME="sdq6" PKNAME="sdq" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="3600507681381022548000000000001ec" KNAME="dm-0" PKNAME="sdq" SIZE="536870912000" LOG-SEC="512" TYPE="mpath" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec1" KNAME="dm-1" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="/" WWN=""
NAME="3600507681381022548000000000001ec6" KNAME="dm-6" PKNAME="dm-0" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="[SWAP]" WWN=""
NAME="3600507681381022548000000000001ec4" KNAME="dm-4" PKNAME="dm-0" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="/boot/efi" WWN=""
NAME="3600507681381022548000000000001ec2" KNAME="dm-2" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec5" KNAME="dm-5" PKNAME="dm-0" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="/var/log" WWN=""
NAME="3600507681381022548000000000001ec3" KNAME="dm-3" PKNAME="dm-0" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="XSLocalEXT--3816bde9--226b--5f15--640c--121ffa6bc20b-3816bde9--226b--5f15--640c--121ffa6bc20b" KNAME="dm-7" PKNAME="dm-3" SIZE="492293849088" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="/run/sr-mount/3816bde9-226b-5f15-640c-121ffa6bc20b" WWN=""
NAME="sdg" KNAME="sdg" PKNAME="" SIZE="536870912000" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdg5" KNAME="sdg5" PKNAME="sdg" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdg3" KNAME="sdg3" PKNAME="sdg" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdg1" KNAME="sdg1" PKNAME="sdg" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdg6" KNAME="sdg6" PKNAME="sdg" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdg4" KNAME="sdg4" PKNAME="sdg" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdg2" KNAME="sdg2" PKNAME="sdg" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="3600507681381022548000000000001ec" KNAME="dm-0" PKNAME="sdg" SIZE="536870912000" LOG-SEC="512" TYPE="mpath" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec1" KNAME="dm-1" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="/" WWN=""
NAME="3600507681381022548000000000001ec6" KNAME="dm-6" PKNAME="dm-0" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="[SWAP]" WWN=""
NAME="3600507681381022548000000000001ec4" KNAME="dm-4" PKNAME="dm-0" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="/boot/efi" WWN=""
NAME="3600507681381022548000000000001ec2" KNAME="dm-2" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec5" KNAME="dm-5" PKNAME="dm-0" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="/var/log" WWN=""
NAME="3600507681381022548000000000001ec3" KNAME="dm-3" PKNAME="dm-0" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="XSLocalEXT--3816bde9--226b--5f15--640c--121ffa6bc20b-3816bde9--226b--5f15--640c--121ffa6bc20b" KNAME="dm-7" PKNAME="dm-3" SIZE="492293849088" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="/run/sr-mount/3816bde9-226b-5f15-640c-121ffa6bc20b" WWN=""
NAME="sde" KNAME="sde" PKNAME="" SIZE="21990232555520" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810286"
NAME="sdn" KNAME="sdn" PKNAME="" SIZE="21990232555520" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810286"
NAME="sdc" KNAME="sdc" PKNAME="" SIZE="21990232555520" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810286"
NAME="sdl" KNAME="sdl" PKNAME="" SIZE="536870912000" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdl5" KNAME="sdl5" PKNAME="sdl" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdl3" KNAME="sdl3" PKNAME="sdl" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdl1" KNAME="sdl1" PKNAME="sdl" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdl6" KNAME="sdl6" PKNAME="sdl" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdl4" KNAME="sdl4" PKNAME="sdl" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdl2" KNAME="sdl2" PKNAME="sdl" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="3600507681381022548000000000001ec" KNAME="dm-0" PKNAME="sdl" SIZE="536870912000" LOG-SEC="512" TYPE="mpath" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec1" KNAME="dm-1" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="/" WWN=""
NAME="3600507681381022548000000000001ec6" KNAME="dm-6" PKNAME="dm-0" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="[SWAP]" WWN=""
NAME="3600507681381022548000000000001ec4" KNAME="dm-4" PKNAME="dm-0" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="/boot/efi" WWN=""
NAME="3600507681381022548000000000001ec2" KNAME="dm-2" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec5" KNAME="dm-5" PKNAME="dm-0" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="/var/log" WWN=""
NAME="3600507681381022548000000000001ec3" KNAME="dm-3" PKNAME="dm-0" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="XSLocalEXT--3816bde9--226b--5f15--640c--121ffa6bc20b-3816bde9--226b--5f15--640c--121ffa6bc20b" KNAME="dm-7" PKNAME="dm-3" SIZE="492293849088" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="/run/sr-mount/3816bde9-226b-5f15-640c-121ffa6bc20b" WWN=""
NAME="sdt" KNAME="sdt" PKNAME="" SIZE="536870912000" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="3600507681381022548000000000001ec" KNAME="dm-0" PKNAME="sdt" SIZE="536870912000" LOG-SEC="512" TYPE="mpath" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec1" KNAME="dm-1" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="/" WWN=""
NAME="3600507681381022548000000000001ec6" KNAME="dm-6" PKNAME="dm-0" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="[SWAP]" WWN=""
NAME="3600507681381022548000000000001ec4" KNAME="dm-4" PKNAME="dm-0" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="/boot/efi" WWN=""
NAME="3600507681381022548000000000001ec2" KNAME="dm-2" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec5" KNAME="dm-5" PKNAME="dm-0" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="/var/log" WWN=""
NAME="3600507681381022548000000000001ec3" KNAME="dm-3" PKNAME="dm-0" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="XSLocalEXT--3816bde9--226b--5f15--640c--121ffa6bc20b-3816bde9--226b--5f15--640c--121ffa6bc20b" KNAME="dm-7" PKNAME="dm-3" SIZE="492293849088" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="/run/sr-mount/3816bde9-226b-5f15-640c-121ffa6bc20b" WWN=""
NAME="sda" KNAME="sda" PKNAME="" SIZE="54975581388800" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x600507638081046d"
NAME="3600507638081046dd800000000000043" KNAME="dm-8" PKNAME="sda" SIZE="54975581388800" LOG-SEC="512" TYPE="mpath" MOUNTPOINT="" WWN=""
NAME="VG_XenStorage--aed646f9--4764--81b7--9675--e79c19ce0a41-MGT" KNAME="dm-9" PKNAME="dm-8" SIZE="4194304" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="" WWN=""
NAME="sdj" KNAME="sdj" PKNAME="" SIZE="21990232555520" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810286"
NAME="sdr" KNAME="sdr" PKNAME="" SIZE="21990232555520" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810286"
NAME="sdh" KNAME="sdh" PKNAME="" SIZE="536870912000" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdh5" KNAME="sdh5" PKNAME="sdh" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdh3" KNAME="sdh3" PKNAME="sdh" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdh1" KNAME="sdh1" PKNAME="sdh" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdh6" KNAME="sdh6" PKNAME="sdh" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdh4" KNAME="sdh4" PKNAME="sdh" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdh2" KNAME="sdh2" PKNAME="sdh" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="3600507681381022548000000000001ec" KNAME="dm-0" PKNAME="sdh" SIZE="536870912000" LOG-SEC="512" TYPE="mpath" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec1" KNAME="dm-1" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="/" WWN=""
NAME="3600507681381022548000000000001ec6" KNAME="dm-6" PKNAME="dm-0" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="[SWAP]" WWN=""
NAME="3600507681381022548000000000001ec4" KNAME="dm-4" PKNAME="dm-0" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="/boot/efi" WWN=""
NAME="3600507681381022548000000000001ec2" KNAME="dm-2" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec5" KNAME="dm-5" PKNAME="dm-0" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="/var/log" WWN=""
NAME="3600507681381022548000000000001ec3" KNAME="dm-3" PKNAME="dm-0" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="XSLocalEXT--3816bde9--226b--5f15--640c--121ffa6bc20b-3816bde9--226b--5f15--640c--121ffa6bc20b" KNAME="dm-7" PKNAME="dm-3" SIZE="492293849088" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="/run/sr-mount/3816bde9-226b-5f15-640c-121ffa6bc20b" WWN=""
NAME="sdp" KNAME="sdp" PKNAME="" SIZE="536870912000" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdp4" KNAME="sdp4" PKNAME="sdp" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdp2" KNAME="sdp2" PKNAME="sdp" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdp5" KNAME="sdp5" PKNAME="sdp" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdp3" KNAME="sdp3" PKNAME="sdq" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdp1" KNAME="sdp1" PKNAME="sdp" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="sdp6" KNAME="sdp6" PKNAME="sdp" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="0x6005076813810225"
NAME="3600507681381022548000000000001ec" KNAME="dm-0" PKNAME="sdp" SIZE="536870912000" LOG-SEC="512" TYPE="mpath" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec1" KNAME="dm-1" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="/" WWN=""
NAME="3600507681381022548000000000001ec6" KNAME="dm-6" PKNAME="dm-0" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="[SWAP]" WWN=""
NAME="3600507681381022548000000000001ec4" KNAME="dm-4" PKNAME="dm-0" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="/boot/efi" WWN=""
NAME="3600507681381022548000000000001ec2" KNAME="dm-2" PKNAME="dm-0" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="3600507681381022548000000000001ec5" KNAME="dm-5" PKNAME="dm-0" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="/var/log" WWN=""
NAME="3600507681381022548000000000001ec3" KNAME="dm-3" PKNAME="dm-0" SIZE="492309560832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="XSLocalEXT--3816bde9--226b--5f15--640c--121ffa6bc20b-3816bde9--226b--5f15--640c--121ffa6bc20b" KNAME="dm-7" PKNAME="dm-3" SIZE="492293849088" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="/run/sr-mount/3816bde9-226b-5f15-640c-121ffa6bc20b" WWN=""
"""

# Minimal: single plain disk, no children
# Minimal: md array across two disks, not mounted
LSBLK_MD_FREE = """\
NAME="nvme0n1" KNAME="nvme0n1" PKNAME="" SIZE="15360950534144" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="md0" KNAME="md0" PKNAME="nvme0n1" SIZE="30721630535680" LOG-SEC="512" TYPE="raid0" MOUNTPOINT="" WWN=""
NAME="nvme1n1" KNAME="nvme1n1" PKNAME="" SIZE="15360950534144" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="md0" KNAME="md0" PKNAME="nvme1n1" SIZE="30721630535680" LOG-SEC="512" TYPE="raid0" MOUNTPOINT="" WWN=""
"""


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------

def _rescan(lsblk_output: str) -> list[Host.BlockDeviceInfo]:
    host = MagicMock(spec=Host)
    host.ssh.return_value = lsblk_output
    Host.rescan_block_devices_info(host)
    return host.block_devices_info


def _by_name(devices: list[Host.BlockDeviceInfo], name: str) -> Host.BlockDeviceInfo:
    matches = [d for d in devices if d.name == name]
    assert len(matches) == 1, f"{name!r} found {len(matches)} times in {[d.name for d in devices]}"
    return matches[0]


# ---------------------------------------------------------------------------
# focused tests
# ---------------------------------------------------------------------------

def test_plain_disk_no_children():
    devices = _rescan(
        'NAME="sda" KNAME="sda" PKNAME="" SIZE="500107862016" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""\n'
    )
    assert len(devices) == 1
    d = devices[0]
    assert d.name == 'sda'
    assert d.path == '/dev/sda'
    assert d.type == 'disk'
    assert d.size == 500107862016
    assert d.log_sec == 512
    assert d.available is True
    assert d.wwn == ''


def test_plain_disk_wwn_stripped():
    devices = _rescan(
        'NAME="sda" KNAME="sda" PKNAME="" SIZE="500107862016" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0x6005076813810286"\n'
    )
    assert devices[0].wwn == '6005076813810286'


def test_disk_with_free_partitions_is_available():
    devices = _rescan("""\
NAME="sda" KNAME="sda" PKNAME="" SIZE="536870912000" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="sda1" KNAME="sda1" PKNAME="sda" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="sda2" KNAME="sda2" PKNAME="sda" SIZE="536334039040" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
""")
    assert len(devices) == 1
    d = devices[0]
    assert d.name == 'sda'
    assert d.type == 'disk'
    assert d.available is True


def test_disk_with_mounted_partition_is_unavailable():
    devices = _rescan("""\
NAME="sda" KNAME="sda" PKNAME="" SIZE="536870912000" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="sda1" KNAME="sda1" PKNAME="sda" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="/" WWN=""
NAME="sda2" KNAME="sda2" PKNAME="sda" SIZE="536334039040" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
""")
    assert len(devices) == 1
    assert devices[0].available is False


def test_disk_with_partition_in_raid_is_unavailable():
    devices = _rescan("""\
NAME="sda" KNAME="sda" PKNAME="" SIZE="536870912000" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="sda1" KNAME="sda1" PKNAME="sda" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="md0" KNAME="md0" PKNAME="sda1" SIZE="536739586048" LOG-SEC="512" TYPE="raid1" MOUNTPOINT="" WWN=""
""")
    # sda is a disk (unavailable), md0 is the raid array
    disk = _by_name(devices, 'sda')
    assert disk.available is False


def test_md_array_detected():
    devices = _rescan(LSBLK_MD_FREE)
    md = _by_name(devices, 'md0')
    assert md.type == 'md'
    assert md.path == '/dev/md0'
    assert md.size == 30721630535680


def test_md_array_deduplicated():
    # md0 appears twice in the lsblk output (once per member disk)
    devices = _rescan(LSBLK_MD_FREE)
    md_devices = [d for d in devices if d.name == 'md0']
    assert len(md_devices) == 1


def test_md_array_free_is_available():
    devices = _rescan(LSBLK_MD_FREE)
    assert _by_name(devices, 'md0').available is True


def test_md_array_mounted_is_unavailable():
    devices = _rescan("""\
NAME="nvme0n1" KNAME="nvme0n1" PKNAME="" SIZE="15360950534144" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="md0" KNAME="md0" PKNAME="nvme0n1" SIZE="30721630535680" LOG-SEC="512" TYPE="raid0" MOUNTPOINT="/mnt/data" WWN=""
NAME="nvme1n1" KNAME="nvme1n1" PKNAME="" SIZE="15360950534144" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="md0" KNAME="md0" PKNAME="nvme1n1" SIZE="30721630535680" LOG-SEC="512" TYPE="raid0" MOUNTPOINT="/mnt/data" WWN=""
""")
    assert _by_name(devices, 'md0').available is False


def test_md_member_disks_are_unavailable():
    devices = _rescan(LSBLK_MD_FREE)
    assert _by_name(devices, 'nvme0n1').available is False
    assert _by_name(devices, 'nvme1n1').available is False


# ---------------------------------------------------------------------------
# full real-world output tests
# ---------------------------------------------------------------------------

def test_full_output_device_count():
    devices = _rescan(LSBLK_FULL)
    disks = [d for d in devices if d.type == 'disk']
    mpaths = [d for d in devices if d.type == 'mpath']
    # 3 unique WWNs across all disks:
    #   sdf  (WWN 0x6005076813810286) - representative of 8 free plain disks
    #   sdd  (WWN 0x6005076813810225) - representative of 8 paths for dm-0
    #   sdo  (WWN 0x600507638081046d) - representative of 4 paths for dm-8
    assert len(disks) == 3
    # 2 mpath devices: dm-0 and dm-8
    assert len(mpaths) == 2


def test_full_output_mpath_deduplicated():
    devices = _rescan(LSBLK_FULL)
    mpaths = [d for d in devices if d.type == 'mpath']
    names = {d.name for d in mpaths}
    assert names == {'dm-0', 'dm-8'}


def test_full_output_mpath_paths():
    devices = _rescan(LSBLK_FULL)
    dm0 = _by_name(devices, 'dm-0')
    dm8 = _by_name(devices, 'dm-8')
    assert dm0.path == '/dev/mapper/3600507681381022548000000000001ec'
    assert dm8.path == '/dev/mapper/3600507638081046dd800000000000043'


def test_full_output_mpath_availability():
    devices = _rescan(LSBLK_FULL)
    # dm-0 has mounted partitions (/, [SWAP], /boot/efi, /var/log) -> unavailable
    assert _by_name(devices, 'dm-0').available is False
    # dm-8 has an lvm child (dm-9) -> unavailable
    assert _by_name(devices, 'dm-8').available is False


def test_full_output_mpath_path_disks_unavailable():
    devices = _rescan(LSBLK_FULL)
    # First-seen path disk for dm-0 and dm-8 must be unavailable (mpath child)
    assert _by_name(devices, 'sdd').available is False
    assert _by_name(devices, 'sdo').available is False


def test_full_output_free_plain_disks_available():
    devices = _rescan(LSBLK_FULL)
    # sdf is the first-seen representative of the 8 free disks sharing the same WWN
    assert _by_name(devices, 'sdf').available is True


def test_full_output_disk_wwn():
    devices = _rescan(LSBLK_FULL)
    # only the first-seen representative per WWN group is present after deduplication
    assert _by_name(devices, 'sdf').wwn == '6005076813810286'
    assert _by_name(devices, 'sdd').wwn == '6005076813810225'
    assert _by_name(devices, 'sdo').wwn == '600507638081046d'


def test_full_output_mpath_wwn():
    devices = _rescan(LSBLK_FULL)
    # mpath NAME is the T10 NAA identifier; strip the leading '3' and truncate to 16 hex chars
    assert _by_name(devices, 'dm-0').wwn == '6005076813810225'
    assert _by_name(devices, 'dm-8').wwn == '600507638081046d'


def test_disk_with_lvm_child_is_unavailable():
    # part -> lvm (mounted): unavailability must propagate up through the partition to the disk
    devices = _rescan("""\
NAME="sda" KNAME="sda" PKNAME="" SIZE="536870912000" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="sda1" KNAME="sda1" PKNAME="sda" SIZE="536334039040" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="myvg-mylv" KNAME="dm-0" PKNAME="sda1" SIZE="536220073984" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="/data" WWN=""
""")
    assert _by_name(devices, 'sda').available is False


# Full lsblk --pairs output from a host with:
#   - sda: boot disk (mounted partitions, lvm child)
#   - sdb: free disk with unused partitions
#   - nvme0n1, nvme1n1: members of md0 (raid0)
LSBLK_RAID1_HOST = """\
NAME="nvme0n1" KNAME="nvme0n1" PKNAME="" SIZE="15360950534144" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="md0" KNAME="md0" PKNAME="nvme0n1" SIZE="30721630535680" LOG-SEC="512" TYPE="raid0" MOUNTPOINT="" WWN=""
NAME="sdb" KNAME="sdb" PKNAME="" SIZE="240057409536" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="sdb9" KNAME="sdb9" PKNAME="sdb" SIZE="8388608" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="sdb1" KNAME="sdb1" PKNAME="sdb" SIZE="240047357952" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="nvme1n1" KNAME="nvme1n1" PKNAME="" SIZE="15360950534144" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="md0" KNAME="md0" PKNAME="nvme1n1" SIZE="30721630535680" LOG-SEC="512" TYPE="raid0" MOUNTPOINT="" WWN=""
NAME="sda" KNAME="sda" PKNAME="" SIZE="240057409536" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="sda4" KNAME="sda4" PKNAME="sda" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="/boot/efi" WWN=""
NAME="sda2" KNAME="sda2" PKNAME="sda" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="sda5" KNAME="sda5" PKNAME="sda" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="/var/log" WWN=""
NAME="sda3" KNAME="sda3" PKNAME="sda" SIZE="195496058368" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="XSLocalEXT--9cf600ab--221b--8deb--a1f6--51354b56fb85-9cf600ab--221b--8deb--a1f6--51354b56fb85" KNAME="dm-0" PKNAME="sda3" SIZE="195483926528" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="/run/sr-mount/9cf600ab-221b-8deb-a1f6-51354b56fb85" WWN=""
NAME="sda1" KNAME="sda1" PKNAME="sda" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="/" WWN=""
NAME="sda6" KNAME="sda6" PKNAME="sda" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="[SWAP]" WWN=""
"""


def test_raid1_host_sdb_free():
    # sdb has only unused partitions -> available
    devices = _rescan(LSBLK_RAID1_HOST)
    assert _by_name(devices, 'sdb').available is True


def test_raid1_host_sda_unavailable():
    # sda has mounted partitions and an lvm child -> unavailable
    devices = _rescan(LSBLK_RAID1_HOST)
    assert _by_name(devices, 'sda').available is False


def test_raid1_host_md0_detected_and_deduplicated():
    devices = _rescan(LSBLK_RAID1_HOST)
    md_devices = [d for d in devices if d.name == 'md0']
    assert len(md_devices) == 1
    assert md_devices[0].type == 'md'
    assert md_devices[0].available is True


def test_raid1_host_nvme_members_unavailable():
    devices = _rescan(LSBLK_RAID1_HOST)
    assert _by_name(devices, 'nvme0n1').available is False
    assert _by_name(devices, 'nvme1n1').available is False


# Full lsblk --pairs output from a simple host with no mpath or raid:
#   - nvme0n1: free disk, no children
#   - sda: boot disk (mounted partitions, lvm child)
LSBLK_SIMPLE_HOST = """\
NAME="nvme0n1" KNAME="nvme0n1" PKNAME="" SIZE="512110190592" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="sda" KNAME="sda" PKNAME="" SIZE="120034123776" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN=""
NAME="sda4" KNAME="sda4" PKNAME="sda" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="/boot/efi" WWN=""
NAME="sda2" KNAME="sda2" PKNAME="sda" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="sda5" KNAME="sda5" PKNAME="sda" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="/var/log" WWN=""
NAME="sda3" KNAME="sda3" PKNAME="sda" SIZE="75499053056" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN=""
NAME="XSLocalEXT--1fa367e3--84ac--b13d--7dc1--6c5ec5ad1808-1fa367e3--84ac--b13d--7dc1--6c5ec5ad1808" KNAME="dm-1" PKNAME="sda3" SIZE="75485585408" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="/run/sr-mount/1fa367e3-84ac-b13d-7dc1-6c5ec5ad1808" WWN=""
NAME="sda1" KNAME="sda1" PKNAME="sda" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="/" WWN=""
NAME="sda6" KNAME="sda6" PKNAME="sda" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="[SWAP]" WWN=""
"""


def test_simple_host_nvme_free():
    devices = _rescan(LSBLK_SIMPLE_HOST)
    assert _by_name(devices, 'nvme0n1').available is True


def test_simple_host_sda_unavailable():
    devices = _rescan(LSBLK_SIMPLE_HOST)
    assert _by_name(devices, 'sda').available is False


# Full lsblk --pairs output from a host with:
#   - nvme0n1: 4K logical sector size, free disk (no children)
#   - nvme1n1: 512B logical sector size, boot disk (mounted partitions, lvm child)
LSBLK_4K_BLOCK_DEVICE = """\
NAME="nvme0n1" KNAME="nvme0n1" PKNAME="" SIZE="1000204886016" LOG-SEC="4096" TYPE="disk" MOUNTPOINT="" WWN="eui.e8238fa6bf530001001b444a41dd2519"
NAME="nvme1n1" KNAME="nvme1n1" PKNAME="" SIZE="512110190592" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="eui.000000000000001000080d0300274f1e"
NAME="nvme1n1p4" KNAME="nvme1n1p4" PKNAME="nvme1n1" SIZE="536870912" LOG-SEC="512" TYPE="part" MOUNTPOINT="/boot/efi" WWN="eui.000000000000001000080d0300274f1e"
NAME="nvme1n1p2" KNAME="nvme1n1p2" PKNAME="nvme1n1" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="eui.000000000000001000080d0300274f1e"
NAME="nvme1n1p5" KNAME="nvme1n1p5" PKNAME="nvme1n1" SIZE="4294967296" LOG-SEC="512" TYPE="part" MOUNTPOINT="/var/log" WWN="eui.000000000000001000080d0300274f1e"
NAME="nvme1n1p3" KNAME="nvme1n1p3" PKNAME="nvme1n1" SIZE="467548839424" LOG-SEC="512" TYPE="part" MOUNTPOINT="" WWN="eui.000000000000001000080d0300274f1e"
NAME="XSLocalEXT--1465252c--889c--e87a--b490--8a9f1dc848b0-1465252c--889c--e87a--b490--8a9f1dc848b0" KNAME="dm-1" PKNAME="nvme1n1p3" SIZE="467534872576" LOG-SEC="512" TYPE="lvm" MOUNTPOINT="/run/sr-mount/1465252c-889c-e87a-b490-8a9f1dc848b0" WWN=""
NAME="nvme1n1p1" KNAME="nvme1n1p1" PKNAME="nvme1n1" SIZE="19327352832" LOG-SEC="512" TYPE="part" MOUNTPOINT="/" WWN="eui.000000000000001000080d0300274f1e"
NAME="nvme1n1p6" KNAME="nvme1n1p6" PKNAME="nvme1n1" SIZE="1073741824" LOG-SEC="512" TYPE="part" MOUNTPOINT="[SWAP]" WWN="eui.000000000000001000080d0300274f1e"
"""


def test_4k_block_device_detected():
    devices = _rescan(LSBLK_4K_BLOCK_DEVICE)
    d = _by_name(devices, 'nvme0n1')
    assert d.path == '/dev/nvme0n1'
    assert d.type == 'disk'
    assert d.size == 1000204886016
    assert d.log_sec == 4 * KiB
    assert d.available is True


# Two paths to the same LUN (same WWN), no multipath configured → deduplicate to one disk entry
LSBLK_NO_MPATH_SAME_WWN = """\
NAME="sda" KNAME="sda" PKNAME="" SIZE="500107862016" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0xAABBCCDD00112233"
NAME="sdb" KNAME="sdb" PKNAME="" SIZE="500107862016" LOG-SEC="512" TYPE="disk" MOUNTPOINT="" WWN="0xAABBCCDD00112233"
"""


def test_no_mpath_same_wwn_deduplicated():
    devices = _rescan(LSBLK_NO_MPATH_SAME_WWN)
    assert len(devices) == 1
    assert devices[0].name == 'sda'


def test_no_mpath_same_wwn_available():
    devices = _rescan(LSBLK_NO_MPATH_SAME_WWN)
    assert devices[0].available is True
