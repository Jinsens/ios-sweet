#!/usr/bin/env python3
"""Opt-in 20H392 bring-up: start flashlight XPC without camera/shader prewarm.

Use on a mounted, offline iOS root image. Defaults to validation only. This
changes two instructions in CMCaptureCore's startup name array and one XML
boolean, preserving file sizes, hardlinks, ownership, modes and xattrs.
It does not provide a working camera, GPU or physical flashlight.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import struct

CACHE = 'System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64.25'
CACHE_SHA256 = 'd871019b58cd52c9923d3368689c30463a57ded847fcb192fe6574560f2a7cfc'
CACHE_UUID = bytes.fromhex('ed459aba225d35c69aab7ae6c303ab8a')
# _FigStartCaptureServers (CMCaptureCore), actual iPhone X 20H392 arm64.
# initWithObjects: first name becomes FigFlashlightServerStart, followed by
# nil as the first Darwin variadic stack argument. The rest are unreachable.
PATCHES = (
    (0x36a08b4, bytes.fromhex('e90300f9'), bytes.fromhex('ff0300f9')),
    (0x36a08bc, bytes.fromhex('42001391'), struct.pack('<I', 0x91000000 | (0x560 << 10) | (2 << 5) | 2)),
)


def prepare(root):
    root = Path(root)
    version = plistlib.loads((root / 'System/Library/CoreServices/SystemVersion.plist').read_bytes())
    if version.get('ProductBuildVersion') != '20H392' or version.get('ProductVersion') != '16.7.16':
        raise ValueError('only iPhone X 16.7.16/20H392 has been analyzed')
    cache = root / CACHE
    original = cache.read_bytes()
    if original[88:104] != CACHE_UUID or hashlib.sha256(original).hexdigest() != CACHE_SHA256:
        raise ValueError('cache UUID/hash mismatch; refusing unverified instructions')
    patched = bytearray(original)
    for offset, before, after in PATCHES:
        if original[offset:offset + len(before)] != before:
            raise ValueError('instruction mismatch at %#x' % offset)
        patched[offset:offset + len(after)] = after
    changes = [(cache, original, bytes(patched))]
    seen = set()
    for board in ('D22', 'D221', 'Default'):
        path = root / ('System/Library/Frameworks/MediaToolbox.framework/' + board + '/SubServerConfiguration.plist')
        st = path.stat()
        identity = (st.st_dev, st.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        data = path.read_bytes()
        parsed = plistlib.loads(data)
        if parsed.get('StartRecorderServer') is not False:
            raise ValueError('expected existing recorder-off GPU workaround')
        anchor = b'<key>StartRecorderServer</key>\n\t<false/>'
        if data.count(anchor) != 1:
            raise ValueError('unrecognized subserver XML layout')
        new = data.replace(anchor, anchor.replace(b'<false/>', b'<true />'))
        expected = dict(parsed, StartRecorderServer=True)
        if len(new) != len(data) or plistlib.loads(new) != expected:
            raise ValueError('subserver edit changes unexpected data')
        changes.append((path, data, new))
    return changes


def apply(changes):
    # Preflight every file before the first mutation; write in place to preserve
    # shared-cache cryptex hardlinks and numeric iOS ownership/permissions.
    for path, before, after in changes:
        if path.read_bytes() != before or len(before) != len(after):
            raise ValueError('file changed after validation: ' + str(path))
    results = []
    for path, before, after in changes:
        stat_before = path.stat()
        with path.open('r+b') as f:
            f.write(after)
            f.flush()
            os.fsync(f.fileno())
        st = path.stat()
        attrs = ('st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_size', 'st_nlink')
        if any(getattr(st, key) != getattr(stat_before, key) for key in attrs) or path.read_bytes() != after:
            raise ValueError('post-write content/metadata check failed: ' + str(path))
        results.append({'path': str(path), 'bytes': len(after),
                        'before_sha256': hashlib.sha256(before).hexdigest(),
                        'after_sha256': hashlib.sha256(after).hexdigest(),
                        'changed_bytes': sum(a != b for a, b in zip(before, after))})
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root', type=Path)
    ap.add_argument('--apply', action='store_true', help='write the validated mounted offline image')
    args = ap.parse_args()
    changes = prepare(args.root)
    if args.apply:
        result = apply(changes)
    else:
        result = [{'path': str(p), 'bytes': len(b),
                   'changed_bytes': sum(x != y for x, y in zip(a, b))} for p, a, b in changes]
    print(json.dumps({'applied': args.apply, 'changes': result}, indent=2))


if __name__ == '__main__':
    main()
