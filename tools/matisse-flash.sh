#!/bin/bash
# Flash a boot image into a matisse slot from recovery, keeping a backup.
#
#   tools/matisse-flash.sh IMAGE [boot_a]
#
# The device must be in a recovery with adb root (OrangeFox). boot_a is read
# back and compared before anything is written; the old contents are kept in
# backup/boot_a-stock.img (gitignored) unless one is already there.
#
# MediaTek LK has no "fastboot boot", so a boot image can only be tried by
# flashing it. Recovery is never touched, so Power + Volume down always gets
# the device back to fastboot, and the stock image restores the slot:
#   fastboot flash boot_a backup/boot_a-stock.img
set -euo pipefail
cd "$(dirname "$0")/.."

IMG=${1:?usage: matisse-flash.sh IMAGE [PARTITION]}
PART=${2:-boot_a}
BACKUP=backup/$PART-stock.img

[ -f "$IMG" ] || { echo "no such image: $IMG" >&2; exit 1; }
mkdir -p backup

adb wait-for-device
adb shell true

if [ ! -f "$BACKUP" ]; then
	echo "backing up $PART -> $BACKUP"
	adb exec-out "dd if=/dev/block/by-name/$PART bs=1048576" > "$BACKUP"
	ls -l "$BACKUP"
fi

echo "image: $IMG ($(stat -c %s "$IMG") bytes)"
adb push "$IMG" /tmp/shim-boot.img >/dev/null
adb shell "sync; dd if=/tmp/shim-boot.img of=/dev/block/by-name/$PART bs=1048576 conv=fsync; sync; rm -f /tmp/shim-boot.img"

# Read the first 2 MiB back and compare with what we meant to write.
adb exec-out "dd if=/dev/block/by-name/$PART bs=1048576 count=2" > /tmp/shim-readback.img
if cmp -s -n "$(stat -c %s "$IMG")" "$IMG" /tmp/shim-readback.img; then
	echo "readback OK"
else
	echo "READBACK MISMATCH - restore with: fastboot flash $PART $BACKUP" >&2
	exit 1
fi

echo "flashed $PART. Reboot with: adb reboot"
echo "to undo: fastboot flash $PART $BACKUP"
