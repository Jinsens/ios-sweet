#!/usr/bin/env python3
"""Repack local iPhone X 20H392 images into a raw HFSX root on macOS.

Run as root to preserve iOS numeric ownership. No device partitions are
accessed. The output must not exist. hdiutil mounts are detached on exit.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time


def run(*args, capture=False):
    print('+', ' '.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('os_image', type=Path)
    ap.add_argument('systemos_image', type=Path)
    ap.add_argument('appos_image', type=Path)
    ap.add_argument('output', type=Path)
    ap.add_argument('--size', default='16g')
    ap.add_argument('--owner', type=int, required=True, help='host owner of completed artifact')
    args = ap.parse_args()
    if sys.platform != 'darwin' or os.geteuid() != 0:
        ap.error('run on macOS as root; iOS ownership must be preserved')
    images = [p.resolve(strict=True) for p in
              (args.os_image, args.systemos_image, args.appos_image)]
    output = args.output.absolute()
    if output.suffix != '.dmg' or output.exists():
        ap.error('output must be a new .dmg path (raw filesystem, no partition map)')
    work = output.with_suffix('.mounts')
    work.mkdir()
    devices = []

    def attach(image, name, readonly=True):
        mount = work / name
        mount.mkdir()
        cmd = ['hdiutil', 'attach', '-plist', '-nobrowse', '-owners', 'on',
               '-mountpoint', mount]
        if readonly:
            cmd.append('-readonly')
        data = plistlib.loads(run(*cmd, image, capture=True))
        entities = data['system-entities']
        devices.append(entities[0]['dev-entry'])
        assert any(e.get('mount-point') == str(mount) for e in entities), data
        return mount

    started = time.time()
    try:
        osroot, sysroot, approot = [attach(p, n) for p, n in
                                   zip(images, ('os', 'systemos', 'appos'))]
        version = plistlib.loads((osroot / 'System/Library/CoreServices/SystemVersion.plist').read_bytes())
        assert version['ProductBuildVersion'] == '20H392', version
        assert version['ProductVersion'] == '16.7.16', version
        run('hdiutil', 'create', '-size', args.size, '-layout', 'NONE',
            '-fs', 'JHFSX', '-volname', 'iOSRoot', output)
        root = attach(output, 'root', readonly=False)

        def copy(source, target):
            target.mkdir(parents=True, exist_ok=True)
            run('/usr/bin/rsync', '-aH', '--numeric-ids', str(source) + '/', str(target) + '/')

        copy(osroot, root)
        for name, source in (('OS', sysroot), ('App', approot)):
            target = root / 'private/preboot/Cryptexes' / name
            copy(source, target)
            # ignition_level=1 does not graft cryptexes: expose missing files
            # at their normal paths with hardlinks, preserving symlinks.
            for directory, dirs, files in os.walk(target):
                for entry in dirs[:] + files:
                    src = Path(directory) / entry
                    relative = src.relative_to(target)
                    dst = root / relative
                    if src.is_symlink():
                        if entry in dirs:
                            dirs.remove(entry)
                        if not os.path.lexists(dst):
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            dst.symlink_to(os.readlink(src))
                            st = src.lstat()
                            os.chown(dst, st.st_uid, st.st_gid, follow_symlinks=False)
                    elif not src.is_dir() and not os.path.lexists(dst):
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        os.link(src, dst)

        def mkdir(relative, uid=0, gid=0, mode=0o755):
            p = root / relative
            p.mkdir(parents=True, exist_ok=True)
            os.chown(p, uid, gid)
            os.chmod(p, mode)
            return p

        for relative, mode in [('diagnostics', 0o751), ('uuidtext', 0o755),
                               ('diagnostics/timesync', 0o755)]:
            p = mkdir('private/var/db/' + relative, 272, 272, mode) / '.migrated'
            p.touch()
            os.chown(p, 272, 272)
            os.chmod(p, 0o644)
        bag = mkdir('private/var/keybags', mode=0o700) / 'systembag.kb'
        bag.write_bytes(plistlib.dumps({'KeyBagKeys': b'SWEET'}))
        os.chmod(bag, 0o600)
        mkdir('private/var/mobile/Library/Preferences', 501, 501)
        tweaks_path = Path(__file__).with_name('sweet-root-tweaks.py')
        spec = importlib.util.spec_from_file_location('sweet_root_tweaks', tweaks_path)
        tweaks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tweaks)
        old_argv = sys.argv
        try:
            sys.argv = [str(tweaks_path), str(root)]
            tweaks.main()
        finally:
            sys.argv = old_argv

        # Framework diagnostics go through the kernel's existing /dev/null
        # tee. /dev/console redirection prevents these jobs from launching.
        launchd_path = root / 'System/Library/xpc/launchd.plist'
        launchd = plistlib.loads(launchd_path.read_bytes())
        for job in launchd['LaunchDaemons'].values():
            if job.get('Label') in ('com.apple.backboardd', 'com.apple.SpringBoard'):
                for key in ('StandardOutPath', 'StandardErrorPath'):
                    job.pop(key, None)
                job.setdefault('EnvironmentVariables', {}).update(
                    OS_ACTIVITY_DT_MODE='YES', CFLOG_FORCE_STDERR='1')
        launchd_path.write_bytes(plistlib.dumps(launchd, fmt=plistlib.FMT_BINARY))

        verified = {}
        for relative in ('sbin/launchd', 'usr/lib/dyld',
                         'System/Library/CoreServices/SpringBoard.app/SpringBoard',
                         'usr/libexec/backboardd'):
            p = root / relative
            st = p.stat()
            assert st.st_uid == 0 and st.st_mode & 0o111, (relative, st)
            assert p.read_bytes()[:4] in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'), relative
            verified[relative] = {'uid': st.st_uid, 'gid': st.st_gid,
                                  'mode': oct(st.st_mode & 0o7777), 'bytes': st.st_size}
        cache = root / 'System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64'
        assert cache.read_bytes()[:16].rstrip(b'\0 ') == b'dyld_v1   arm64'
        cryptex_cache = root / 'private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld'
        assert cache.stat().st_ino == (cryptex_cache / cache.name).stat().st_ino
        assert len(list(cache.parent.glob('dyld_shared_cache_arm64*'))) >= 40
        run('/bin/sync')
        # Detach before filesystem verification and hashing.
        run('hdiutil', 'detach', devices[-1])
        devices.pop()
        raw = output.open('rb')
        with raw:
            raw.seek(1024)
            assert raw.read(2) == b'HX', 'output is not a raw HFSX volume'
        # macOS fsck_hfs requires a device, not a regular raw-image file.
        check = plistlib.loads(run('hdiutil', 'attach', '-nomount', '-readonly',
                                   '-plist', output, capture=True))
        device = check['system-entities'][0]['dev-entry']
        devices.append(device)
        run('/sbin/fsck_hfs', '-fn', '/dev/r' + Path(device).name)
        run('hdiutil', 'detach', device)
        devices.pop()
        digest = hashlib.sha256()
        with output.open('rb') as f:
            for block in iter(lambda: f.read(8 << 20), b''):
                digest.update(block)
        meta = {'build': '20H392', 'version': '16.7.16',
                'bytes': output.stat().st_size, 'sha256': digest.hexdigest(),
                'filesystem': 'raw case-sensitive journaled HFSX',
                'elapsed_seconds': round(time.time() - started, 2),
                'verified_binaries': verified, 'device_writes': False}
        metadata = output.with_suffix('.json')
        metadata.write_text(json.dumps(meta, indent=2) + '\n')
        for p in (output, metadata):
            os.chown(p, args.owner, -1)
        print(json.dumps(meta, indent=2), flush=True)
    finally:
        for device in reversed(devices):
            subprocess.run(['hdiutil', 'detach', device], check=False)


if __name__ == '__main__':
    main()
