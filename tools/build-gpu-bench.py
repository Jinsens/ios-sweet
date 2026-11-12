#!/usr/bin/env python3
"""Build a standalone iOS GPU benchmark; never installs or writes a device."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import struct
import subprocess
import zlib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    return subprocess.run([str(x) for x in args], check=True,
                          capture_output=True, text=True).stdout


def icon(path, size):
    """Draw the benchmark's simple graph icon without external dependencies."""
    pixels = bytearray()
    graph = [(0.19, .70), (.34, .56), (.45, .62), (.61, .35), (.73, .43), (.84, .24)]
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            px, py = x / size, y / size
            color = (int(19 + py * 8), int(30 + py * 15), int(58 + px * 27), 255)
            if .16 < px < .87 and abs(py - .76) < .009:
                color = (71, 87, 121, 255)
            for (ax, ay), (bx, by) in zip(graph, graph[1:]):
                t = max(0, min(1, ((px-ax)*(bx-ax)+(py-ay)*(by-ay))/((bx-ax)**2+(by-ay)**2)))
                if (px-ax-t*(bx-ax))**2+(py-ay-t*(by-ay))**2 < .00025:
                    color = (74, 224, 196, 255)
            pixels.extend(color)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, default=ROOT/'research/matisse/gpu-bench-out')
    ap.add_argument('--mesa', type=Path, help='hardware Panfrost libOSMesa.8.dylib to bundle')
    ap.add_argument('--developer', type=Path, default=Path(os.environ.get('DEVELOPER_DIR',
                    '/Users/xiaohuasheng/Downloads/Xcode.app/Contents/Developer')))
    a = ap.parse_args()
    developer = a.developer.resolve()
    sdk = developer/'Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk'
    tool = developer/'Toolchains/XcodeDefault.xctoolchain/usr/bin'
    if not sdk.exists() or not (tool/'clang').exists():
        ap.error('iPhoneOS SDK and Xcode clang are required')
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    app = out/'Payload/SweetGPUBench.app'
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    source = ROOT/'userland/gpu-bench'
    info = plistlib.loads((source/'Info.plist').read_bytes())
    info['CFBundleIcons'] = {'CFBundlePrimaryIcon': {'CFBundleIconFiles': ['AppIcon'],
                                                'UIPrerenderedIcon': True}}
    (app/'Info.plist').write_bytes(plistlib.dumps(info))
    icon(app/'AppIcon@2x.png', 120)
    icon(app/'AppIcon@3x.png', 180)
    sources = [source/'main.m', source/'SweetGPUEngine.m']
    args = [tool/'clang', '-target', 'arm64-apple-ios16.0', '-isysroot', sdk,
            '-fobjc-arc', '-fblocks', '-O2', '-Wall', '-Wextra', '-Werror',
            '-Wno-deprecated-declarations', '-Wl,-rpath,@executable_path/Frameworks',
            '-framework', 'UIKit', '-framework', 'Foundation', '-framework', 'QuartzCore',
            '-framework', 'CoreGraphics',
            *sources, '-o', app/'SweetGPUBench']
    compiled = subprocess.run([str(x) for x in args], capture_output=True, text=True)
    (out/'compile.log').write_text(shlex.join([str(x) for x in args])+'\n'+
                                   compiled.stdout+compiled.stderr)
    if compiled.returncode:
        print(compiled.stdout, end='')
        print(compiled.stderr, end='')
        raise SystemExit(compiled.returncode)
    bundled = []
    if a.mesa:
        mesa = a.mesa.resolve()
        if not mesa.is_file():
            ap.error('--mesa must name an existing hardware library')
        if mesa.name != 'libOSMesa.8.dylib':
            ap.error('--mesa must use the runtime lookup name libOSMesa.8.dylib')
        frameworks = app/'Frameworks'
        frameworks.mkdir()
        pending = [mesa]
        while pending:
            src = pending.pop(0)
            dst = frameworks/src.name
            if dst.exists():
                continue
            shutil.copy2(src, dst)
            dependencies = run(['/usr/bin/otool', '-L', src]).splitlines()[2:]
            run(['/usr/bin/install_name_tool', '-id', '@rpath/'+src.name, dst])
            for line in dependencies:
                dep = line.strip().split(' (', 1)[0]
                if dep.endswith('.dylib') and not dep.startswith(('/usr/lib/', '/System/')):
                    local = mesa.parent/Path(dep).name
                    if not local.is_file():
                        raise SystemExit('Missing non-system Mesa dependency: '+dep)
                    run(['/usr/bin/install_name_tool', '-change', dep, '@loader_path/'+local.name, dst])
                    pending.append(local)
            run(['/usr/bin/codesign', '--force', '--sign', '-', dst])
            bundled.append(src.name)
    # This port runs ad-hoc signed binaries; no Apple certificate is bundled.
    run(['/usr/bin/codesign', '--force', '--sign', '-', app/'SweetGPUBench'])
    run(['/usr/bin/codesign', '--force', '--sign', '-', app])
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
    architecture = run(['/usr/bin/lipo', '-archs', app/info['CFBundleExecutable']]).strip()
    if architecture != 'arm64':
        raise SystemExit('Expected plain arm64 executable, got: '+architecture)
    linked = run(['/usr/bin/otool', '-L', app/info['CFBundleExecutable']])
    if 'OpenGLES.framework' in linked or 'OpenGL.framework' in linked:
        raise SystemExit('Unexpected Apple GL dependency; Mesa entry points must be dynamic')
    (out/'verify.log').write_text('codesign --verify --deep --strict: passed\n'+
                                 'architecture: '+architecture+'\n'+linked)
    ipa = out/'SweetGPUBench.ipa'
    if ipa.exists():
        ipa.unlink()
    run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app.parent, ipa])
    with zipfile.ZipFile(ipa) as package:
        packaged_info = plistlib.loads(package.read('Payload/SweetGPUBench.app/Info.plist'))
        package.getinfo('Payload/SweetGPUBench.app/'+packaged_info['CFBundleExecutable'])
        if package.testzip() is not None:
            raise SystemExit('IPA integrity check failed')
    manifest = {
        'target': 'arm64-apple-ios16.0', 'bundle_id': info['CFBundleIdentifier'],
        'target_fps': [60, 120], 'resolution': [512, 512], 'hardware_required': True,
        'physical_panel_mode_switch': False, 'installed_on_device': False,
        'signing': 'ad-hoc for this XNU port', 'bundled_libraries': bundled,
        'verification': {'architecture': architecture, 'codesign': 'passed', 'ipa_integrity': 'passed'},
        'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sources+[source/'SweetGPUEngine.h', source/'Info.plist']},
        'ipa_sha256': hashlib.sha256(ipa.read_bytes()).hexdigest(), 'ipa_bytes': ipa.stat().st_size,
    }
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(ipa)
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
