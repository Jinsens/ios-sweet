#!/usr/bin/env bash
#
# Incremental local XNU build from the CI build tree.
#
# The CI (xnu.yml) leaves a complete object tree behind: sources, generated
# headers (EXPORT_HDRS, mig output, IOKit tables) and every .o, plus the
# fakeroot that holds libfirehose_kernel.a. Rebuilding the kernel from that
# tree needs no KDK download and no full recompile - only the files that
# changed and the final link.
#
# Usage: tools/build-xnu-local.sh <build-tree> [target]
#   <build-tree>  a directory shaped like xnu/out/ci-*/  (xnu/, build/xnu.obj,
#                 fakeroot/) - e.g. xnu/out/local
#
# Xcode: DEVELOPER_DIR is honoured, so a second Xcode install works without
# xcode-select (which would need sudo):
#   DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer tools/build-xnu-local.sh ...
set -o errexit
set -o nounset
set -o pipefail

TREE=${1:?usage: build-xnu-local.sh <build-tree>}
TARGET=${2:-kernel}
WORK=$(cd "$TREE" && pwd)
REPO=$(cd "$(dirname "$0")/.." && pwd)

# Xcode 16 moved iig into the toolchain and dropped usr/lib/libcc_kext.a;
# clamp the minimum to what the CI link used so the two link the same way.
export DEVELOPER_DIR=${DEVELOPER_DIR:-$(xcode-select -p)}
if [[ ! -x "$DEVELOPER_DIR/usr/bin/xcodebuild" ]]; then
	echo "no Xcode at $DEVELOPER_DIR (Command Line Tools only?)" >&2
	echo "set DEVELOPER_DIR=<Xcode.app>/Contents/Developer" >&2
	exit 1
fi

XCT=$DEVELOPER_DIR/Toolchains/XcodeDefault.xctoolchain/usr/bin
export PATH="$XCT:$DEVELOPER_DIR/usr/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# The dSYM step copies LLDB's Python macros with `env python`; there is no
# python(1) on a stock macOS. The copy is cosmetic (debugger scripts), so a
# shim is enough and keeps `make install` from failing at the last step.
mkdir -p "$WORK/.bin"
printf '#!/bin/sh\nexec /usr/bin/true "$@"\n' > "$WORK/.bin/python"
chmod +x "$WORK/.bin/python"
export PATH="$WORK/.bin:$PATH"

for tool in clang ld iig; do
	if [[ ! -x "$XCT/$tool" ]]; then
		echo "missing $tool in $XCT" >&2
		exit 1
	fi
done

# cctools' CTF tools are not in Xcode; the CI artifact kept them.
mkdir -p "$WORK/fakeroot/usr/local/bin" "$WORK/fakeroot/usr/local/lib/kernel"
for tool in ctfconvert ctfmerge; do
	if [[ ! -x "$WORK/fakeroot/usr/local/bin/$tool" ]]; then
		echo "missing $WORK/fakeroot/usr/local/bin/$tool" >&2
		exit 1
	fi
done
if [[ ! -f "$WORK/fakeroot/usr/local/lib/kernel/libfirehose_kernel.a" ]]; then
	echo "missing fakeroot/usr/local/lib/kernel/libfirehose_kernel.a" >&2
	exit 1
fi

mkdir -p "$WORK/build/xnu.sym" "$WORK/kdk"

# Pin the version banner to the source drop so `uname -v` names the tree
# instead of this machine's clock (see kernel_version_env in dxb/build.sh).
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
