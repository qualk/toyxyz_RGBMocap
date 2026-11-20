"""Derive installer/runtime-lock-linux.json from the Windows lock: same packages and versions, Linux wheels.

The Linux GPU profile uses AMD ROCm PyTorch instead of NVIDIA CUDA PyTorch. The
mapping comes from mocap/runtime_registry.gpu_pins() so the registry and lock
cannot drift; ROCm-only dependencies (device kernels, Triton, ROCm SDK libraries)
are added at their exact pins. Pure-Python wheels and bundled sources are copied
verbatim. Wheel metadata and uncompressed sizes are read from each wheel's zip
directory with HTTP range requests, so the free-space estimate reflects the real
installed size. Nothing is installed.

Maintainer tool; run with any Python that has `packaging`, after changing the
Windows lock or runtime_registry.py. Windows wheels are handled by the Windows
lock and are not touched here.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, io, json, os, re, sys, urllib.parse, urllib.request, zipfile
from email.parser import HeaderParser
from pathlib import Path
from packaging.requirements import Requirement
from packaging.tags import Tag, compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {'User-Agent': 'RGBMoCap-LockTool/1'}
PYTORCH_INDEX = 'https://download.pytorch.org/whl/cu128/'
AMD_INDEX = 'https://stable.repo.amd.com/rocm/whl-next/'
ALLOWED_HOSTS = {'files.pythonhosted.org', 'download.pytorch.org', 'download-r2.pytorch.org', 'stable.repo.amd.com'}
# Newest glibc up to 2.28 first (RHEL 8 / Ubuntu 20.04 era); newer tags only when nothing older exists.
GLIBC = [*range(28, 4, -1), *range(29, 40)]
PLATFORMS = [f'manylinux_2_{minor}_x86_64' for minor in GLIBC] + ['linux_x86_64']
LEGACY = {'manylinux2014_x86_64': 'manylinux_2_17_x86_64', 'manylinux2010_x86_64': 'manylinux_2_12_x86_64',
          'manylinux1_x86_64': 'manylinux_2_5_x86_64'}
BASE_ENV = {'os_name': 'posix', 'sys_platform': 'linux', 'platform_system': 'Linux', 'platform_machine': 'x86_64',
            'implementation_name': 'cpython', 'platform_python_implementation': 'CPython', 'extra': ''}
WINDOWS_ENV = {**BASE_ENV, 'os_name': 'nt', 'sys_platform': 'win32', 'platform_system': 'Windows', 'platform_machine': 'AMD64'}

# ROCm wheels do not carry a SHA-256 fragment in the AMD simple index and their
# extra dependencies are not expressed as Linux-only markers. Pin them here.
ROCM_SDK_VERSION = '10.1.0'
ROCM_BOOTSTRAP_VERSION = '0.3.0'
# RDNA4 by default (Radeon RX 9060 XT is gfx1200). Regenerate the lock with
# RGB_MOCAP_ROCM_DEVICE=<arch> for another AMD GPU whose ROCm PyTorch wheels
# publish matching `amd-torch-device-*` and `rocm-sdk-device-*` artifacts.
ROCM_DEVICE = os.environ.get('RGB_MOCAP_ROCM_DEVICE', 'gfx1200')
AMD_ONLY_NAMES = {'rocm-bootstrap', 'rocm-sdk-core', 'rocm-sdk-libraries',
                  'rocm-sdk-device-' + ROCM_DEVICE}


def registry():
    path = ROOT / 'mocap/runtime_registry.py'
    spec = importlib.util.spec_from_file_location('runtime_registry', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers={**HEADERS, **(headers or {})}), timeout=90) as response:
        return response.status, response.headers, response.read()


class RemoteFile(io.RawIOBase):
    """Seekable read-only view of a remote file; zipfile only touches its directory and chosen members."""
    def __init__(self, url, size):
        self.url, self.size, self.pos = url, size, 0
    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos
    def seek(self, offset, whence=0):
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos
    def readinto(self, buffer):
        if self.pos >= self.size: return 0
        end = min(self.size, self.pos + len(buffer)) - 1
        status, _, data = get(self.url, {'Range': f'bytes={self.pos}-{end}'})
        if status != 206: raise RuntimeError('The server ignored a range request: ' + self.url)
        buffer[:len(data)] = data; self.pos += len(data)
        return len(data)


def inspect_wheel(url, size):
    """Uncompressed install size and Requires-Dist entries of a remote wheel."""
    with zipfile.ZipFile(io.BufferedReader(RemoteFile(url, size), buffer_size=512 * 1024)) as wheel:
        metadata = next(i for i in wheel.infolist() if re.fullmatch(r'[^/]+\.dist-info/METADATA', i.filename))
        headers = HeaderParser().parsestr(wheel.read(metadata).decode('utf-8'))
        return sum(i.file_size for i in wheel.infolist()), headers.get_all('Requires-Dist') or []


def hash_and_size(url):
    """AMD's simple index has no sha256 fragment; hash the wheel while sizing it."""
    digest = hashlib.sha256(); count = 0
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=180) as response:
        while True:
            block = response.read(4 * 1024 ** 2)
            if not block: break
            digest.update(block); count += len(block)
    return digest.hexdigest(), count


def candidates(name, version):
    """(filename, url, sha256, bytes) for every wheel of one release."""
    rocm = 'rocm' in version or canonicalize_name(name) in AMD_ONLY_NAMES
    index = AMD_INDEX if rocm else PYTORCH_INDEX
    if rocm or '+' in version:  # PyTorch/AMD indexes instead of PyPI for local builds.
        page_url = index + canonicalize_name(name) + '/'
        _, _, page = get(page_url)
        rows = []
        for href, text in re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', page.decode('utf-8')):
            link = urllib.parse.urljoin(page_url, href)
            link, _, fragment = link.partition('#sha256=')
            if not text.endswith('.whl'): continue
            if fragment:
                rows.append((urllib.parse.unquote(text), link, fragment, None))
            elif rocm:
                rows.append((urllib.parse.unquote(text), link, None, None))
        return rows
    _, _, body = get(f'https://pypi.org/pypi/{canonicalize_name(name)}/{urllib.parse.quote(version)}/json')
    return [(f['filename'], f['url'], f['digests']['sha256'], f['size'])
            for f in json.loads(body)['urls'] if f['packagetype'] == 'bdist_wheel']


def linux_wheel(name, version, python):
    major, minor = map(int, python.split('.')[:2])
    order = {tag: i for i, tag in enumerate([*cpython_tags((major, minor), platforms=PLATFORMS),
                                              *compatible_tags((major, minor), platforms=PLATFORMS)])}
    def rank(filename):
        tags = parse_wheel_filename(filename)[3]
        return min((order.get(Tag(t.interpreter, t.abi, LEGACY.get(t.platform, t.platform)), 10**9) for t in tags), default=10**9)
    wanted = Version(version)
    found = [row for row in candidates(name, version) if parse_wheel_filename(row[0])[1] == wanted]
    best = min(found, key=lambda row: rank(row[0]), default=None)
    if best is None or rank(best[0]) == 10**9:
        raise RuntimeError(f'No Linux x86_64 wheel for {name}=={version} (Python {python}).')
    if best[3] is None:
        if best[2] is None:
            best = (*best[:2], *hash_and_size(best[1]))
        else:
            _, headers, _ = get(best[1], {'Range': 'bytes=0-0'})
            best = (*best[:3], int(headers['Content-Range'].rsplit('/', 1)[1]))
    return best


def entry(name, version, row):
    filename, url, digest, size = row
    if urllib.parse.urlsplit(url).hostname not in ALLOWED_HOSTS:
        raise RuntimeError('Unapproved wheel host for ' + filename + ': ' + url)
    return dict(name=name, version=version, filename=filename, url=url, sha256=digest, bytes=size)


def bundled_rocm():
    """Hash the deterministic wheel built from the sdist-only `rocm` shim source."""
    source = ROOT / 'installer/runtime_sources/rocm-10.1.0-py3-none-any'
    filename = 'rocm-10.1.0-py3-none-any.whl'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as wheel:
        for path in sorted(source.rglob('*')):
            if not path.is_file() or path.name == 'SOURCE_WHEEL.json' or '__pycache__' in path.parts or path.suffix == '.pyc': continue
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED; info.external_attr = 0o644 << 16
            wheel.writestr(info, path.read_bytes())
    payload = stream.getvalue()
    return dict(name='rocm', version=ROCM_SDK_VERSION, filename=filename, url=None,
                bundled='wheels/' + filename, sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload),
                origin='Official AMD source distribution; rebuilt offline from validated pure-Python sources')


def convert_group(group, base_names, gpu_map, extra):
    python = group['python']; env = {**BASE_ENV, 'python_version': '.'.join(python.split('.')[:2]), 'python_full_version': python}
    win_env = {**WINDOWS_ENV, 'python_version': env['python_version'], 'python_full_version': python}
    packages, installed, queue = [], 0, []
    names = {canonicalize_name(p['name']) for p in group['packages']} | base_names
    for package in group['packages']:
        if package.get('bundled'):
            packages.append(dict(package));installed += package['bytes'] * 3  # rebuilt pure sources; compressed estimate
            continue
        key = canonicalize_name(package['name'])
        version = gpu_map.get(key, package['version'])
        pure = package['filename'].endswith('-none-any.whl') and key not in AMD_ONLY_NAMES
        row = (package['filename'], package['url'], package['sha256'], package['bytes']) if pure else linux_wheel(key, version, python)
        queue.append((key, version, row))
        if key == 'torch' and extra:
            for name, pinned in extra:
                if canonicalize_name(name) not in names:
                    names.add(canonicalize_name(name));queue.append((name, pinned, linux_wheel(name, pinned, python)))
    while queue:
        name, version, row = queue.pop(0)
        size, requires = inspect_wheel(row[1], row[3])
        packages.append(entry(name, version, row));installed += size
        print(f"  {group['key']}: {row[0]} ({size / 1024**2:.0f} MiB installed)", flush=True)
        for text in requires:
            requirement = Requirement(text)
            if requirement.marker is None or not requirement.marker.evaluate(env) or requirement.marker.evaluate(win_env):
                continue  # Only requirements that Linux needs and Windows does not are added.
            key = canonicalize_name(requirement.name)
            if key in names: continue
            pins = [s.version for s in requirement.specifier if s.operator == '==']
            if len(pins) != 1: raise RuntimeError(f'{name} needs unpinned Linux-only {text}; pin it explicitly.')
            names.add(key);queue.append((key, pins[0], linux_wheel(key, pins[0], python)))
    packages.sort(key=lambda p: canonicalize_name(p['name']))
    return dict(group, packages=packages), installed


def lock_id(lock):
    return hashlib.sha256(json.dumps({k: v for k, v in lock.items() if k != 'id'}, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--uv-sha256', required=True, help='SHA-256 of the extracted Linux uv binary for the pinned uv_version')
    parser.add_argument('--output', type=Path, default=ROOT / 'installer/runtime-lock-linux.json')
    parser.add_argument('--gpu', choices=('rocm', 'cuda'), default='rocm', help='GPU backend of the Linux runtime (default: rocm)')
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{64}', args.uv_sha256): raise SystemExit('Invalid --uv-sha256')
    pins = registry()
    torch_pin, torchvision_pin = pins.gpu_pins(args.gpu == 'rocm')
    gpu_map = {'torch': torch_pin, 'torchvision': torchvision_pin}
    extra = []
    if args.gpu == 'rocm':
        extra = [('triton', '3.8.0+git669b31ac.rocm10.1.0'),
                 ('amd-torch-device-' + ROCM_DEVICE, torch_pin),
                 ('amd-torch-device-gfx12-0', torch_pin),
                 # torchvision keeps its compiled ops (NMS, ROI align, ...) in a
                 # separate per-architecture kernel package; without it OpenCV-
                 # style detection fails with hipErrorInvalidDeviceFunction.
                 ('amd-torchvision-device-' + ROCM_DEVICE, torchvision_pin),
                 ('rocm-bootstrap', ROCM_BOOTSTRAP_VERSION),
                 ('rocm-sdk-core', ROCM_SDK_VERSION),
                 ('rocm-sdk-libraries', ROCM_SDK_VERSION),
                 ('rocm-sdk-device-' + ROCM_DEVICE, ROCM_SDK_VERSION)]
    windows = json.loads((ROOT / 'installer/runtime-lock.json').read_text(encoding='utf-8'))
    groups, sizes, names = [], {}, {}
    bundled = bundled_rocm() if args.gpu == 'rocm' else None
    for group in windows['groups']:
        print('Group', group['key'], flush=True)
        base = names.get(group['base'], set()) if group['base'] else set()
        converted, sizes[group['key']] = convert_group(group, base, gpu_map, extra)
        if bundled and any(p['name'] == 'torch' for p in converted['packages']):
            converted['packages'].append(dict(bundled))
            sizes[group['key']] += sum(p.stat().st_size for p in
                (ROOT / 'installer/runtime_sources/rocm-10.1.0-py3-none-any').rglob('*') if p.is_file())
            converted['packages'].sort(key=lambda p: canonicalize_name(p['name']))
        names[group['key']] = {canonicalize_name(p['name']) for p in converted['packages']}
        groups.append(converted)
    unique = {p['sha256']: p['bytes'] for g in groups for p in g['packages'] if p.get('url')}
    download, installed = sum(unique.values()), sum(sizes.values())
    lock = dict(windows, groups=groups, download_bytes=download, installed_bytes_estimate=installed,
                # Verified downloads + installed runtimes + the largest group's transient extraction + 2 GiB margin.
                fresh_free_bytes_required=download + installed + max(sizes.values()) + 2 * 1024**3,
                uv_sha256=args.uv_sha256, gpu_backend=args.gpu,
                registry_sha256=hashlib.sha256((ROOT / 'mocap/runtime_registry.py').read_bytes()).hexdigest())
    lock['id'] = lock_id(lock)
    temporary = args.output.with_name(args.output.name + '.partial')
    temporary.write_text(json.dumps(lock, indent=2) + '\n', encoding='utf-8');temporary.replace(args.output)
    print(f'Wrote {args.output}: {download / 1024**3:.2f} GiB download, {installed / 1024**3:.2f} GiB installed, '
          f'{lock["fresh_free_bytes_required"] / 1024**3:.2f} GiB free space required.')


if __name__ == '__main__':
    sys.exit(main())
