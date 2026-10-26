#!/bin/bash
# Same as tools/build-xnu-local.sh, but without the Xcode.app check: this Mac
# only has Command Line Tools, which carry clang/ld/dsymutil and the macOS SDK.
set -o errexit -o nounset -o pipefail

WORK=/Users/xiaohuasheng/Documents/ios-sweet/xnu/out/local
TARGET=kernel

export DEVELOPER_DIR=/Library/Developer/CommandLineTools
XCT=$DEVELOPER_DIR/usr/bin
export PATH="$XCT:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$WORK/.bin"
printf '#!/bin/sh\nexec /usr/bin/true "$@"\n' > "$WORK/.bin/python"
chmod +x "$WORK/.bin/python"
export PATH="$WORK/.bin:$PATH"

mkdir -p "$WORK/build/xnu.sym" "$WORK/kdk"

export KERNEL_BUILD_DATE=${KERNEL_BUILD_DATE:-"Thu Jun  1 00:00:00 UTC 2023"}
export KERNEL_BUILD_OBJROOT=${KERNEL_BUILD_OBJROOT:-xnu-8796.101.5}

COMMON=(
	SDKROOT=macosx
	TARGET_CONFIGS="DEVELOPMENT ARM64 SWEET"
	CONCISE=0
	LOGCOLORS=y
	BUILD_WERROR=0
	BUILD_LTO=0
	SRCROOT="$WORK/xnu"
	OBJROOT="$WORK/build/xnu.obj"
	SYMROOT="$WORK/build/xnu.sym"
	DSTROOT="$WORK/fakeroot"
	FAKEROOT_DIR="$WORK/fakeroot"
	KDKROOT="$WORK/kdk"
	TIGHTBEAMC=tightbeamc-not-supported
	RC_DARWIN_KERNEL_VERSION=22.6.0
	CTFCONVERT="$WORK/fakeroot/usr/local/bin/ctfconvert"
	CTFMERGE="$WORK/fakeroot/usr/local/bin/ctfmerge"
	CTFDUMP=/usr/bin/true
)

cd "$WORK/xnu"
jobs=$(sysctl -n hw.ncpu)
echo "building $TARGET in $WORK with -j$jobs"
case "$TARGET" in
kernel)	make install -j"$jobs" "${COMMON[@]}" ;;
*)	make "$TARGET" -j"$jobs" "${COMMON[@]}" ;;
esac

ls -l "$WORK/fakeroot/System/Library/Kernels/" 2>/dev/null || true
