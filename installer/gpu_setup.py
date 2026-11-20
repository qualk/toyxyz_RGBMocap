"""Select GPU wheels for a managed runtime at setup time.

Base locks keep the CUDA wheels. When setup detects an AMD GPU, this module
replaces the CUDA-only package entries with the exact ROCm wheels for the
detected platform, Python version and gfx architecture. All wheels are fetched
over HTTPS, hashed and cached before installation, so the existing
size/SHA-256 verification in installer/engine.py is unchanged.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email.parser import HeaderParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AMD_INDEX = 'https://stable.repo.amd.com/rocm/whl-next/'
PINS = {
    'linux': {'torch': '2.14.0+rocm10.1.0', 'torchvision': '0.29.0a0+rocm10.1.0'},
    'windows': {'torch': '2.12.0+rocm10.1.0', 'torchvision': '0.27.0+rocm10.1.0'},
}
PLATFORM_TAG = {'linux': 'linux_x86_64', 'windows': 'win_amd64'}
COMMON = {
    'linux': [('triton', '3.8.0+git669b31ac.rocm10.1.0')],
    'windows': [],
}
GPU_PACKAGE_NAMES = {'torch', 'torchvision', 'nvidia-ml-py', 'triton', 'nvidia-cublas-cu12',
    'nvidia-cuda-cupti-cu12', 'nvidia-cuda-nvrtc-cu12', 'nvidia-cuda-runtime-cu12',
    'nvidia-cudnn-cu12', 'nvidia-cufft-cu12', 'nvidia-cufile-cu12', 'nvidia-curand-cu12',
    'nvidia-cusolver-cu12', 'nvidia-cusparse-cu12', 'nvidia-cusparselt-cu12',
    'nvidia-nccl-cu12', 'nvidia-nvjitlink-cu12', 'nvidia-nvtx-cu12'}


NETWORK_ERRORS = (urllib.error.URLError, OSError, http.client.HTTPException)


def _retry(action, attempts=4, label='request'):
    """Retry transient network failures with bounded exponential backoff."""
    for attempt in range(attempts):
        try:
            return action()
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == attempts - 1:
                raise
            error = 'HTTP %d' % exc.code
        except NETWORK_ERRORS as exc:
            if attempt == attempts - 1:
                raise
            error = str(exc) or type(exc).__name__
        print('Connection problem fetching %s (%s); retrying %d/%d...' %
              (label, error, attempt + 2, attempts), flush=True)
        time.sleep(min(2 ** attempt, 8))


def _get(url, headers=None, timeout=90):
    def once():
        request = urllib.request.Request(url, headers={'User-Agent': 'RGBMoCap-Setup/1', **(headers or {})})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers, response.read()
    return _retry(once, label=url)


class _RemoteFile(io.RawIOBase):
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
        status, _, data = _get(self.url, {'Range': f'bytes={self.pos}-{end}'})
        if status != 206: raise RuntimeError('The wheel server ignored a range request: ' + self.url)
        buffer[:len(data)] = data; self.pos += len(data)
        return len(data)


def _canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def _index(name):
    """All AMD wheel links for one distribution."""
    page_url = AMD_INDEX + urllib.parse.quote(_canonical(name)) + '/'
    _, _, page = _get(page_url)
    rows = []
    for href, text in re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', page.decode('utf-8')):
        if text.endswith('.whl'):
            rows.append((urllib.parse.unquote(text), urllib.parse.urljoin(page_url, href)))
    return rows


def _select(name, version, python, platform):
    major, minor = (python.split('.')[:2] if python else ('3', '10'))
    tag = f'cp{major}{minor}-cp{major}{minor}'
    py3 = 'py3-none'
    plat = PLATFORM_TAG[platform]
    wanted = (_canonical(name) + '-' + version + '-').lower()
    candidates = []
    for filename, url in _index(name):
        raw = filename.lower()
        low = raw.replace('_', '-')  # wheel names may use underscores for the dist name
        if not low.startswith(wanted): continue
        if platform == 'windows':
            pure = f'-{py3}-any.whl' in raw
            if not pure and f'-{tag}-{plat}.whl' not in raw and f'-{py3}-{plat}.whl' not in raw: continue
        else:
            pure = f'-{py3}-any.whl' in raw
            arch_specific = (f'-{tag}-{plat}.whl' in raw or f'-{py3}-{plat}.whl' in raw or
                             ('manylinux' in raw and 'x86_64.whl' in raw))
            if not pure and not arch_specific: continue
        candidates.append((filename, url))
    if not candidates:
        raise RuntimeError(f'No AMD ROCm wheel for {name}=={version} (Python {python}, {platform}).')
    return candidates[0]


def _file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _wheel_manifest(cache):
    path = cache / 'rocm-wheels.json'
    try:
        return path, json.loads(path.read_text(encoding='utf8'))
    except (OSError, ValueError):
        return path, {}


def _hash_and_cache(filename, url, cache):
    """Hash, cache and reuse one wheel; return an installer package entry."""
    manifest_path, manifest = _wheel_manifest(cache)
    cached = manifest.get(url)
    if isinstance(cached, dict) and cached.get('filename') == filename:
        wheel = cache / cached.get('sha256', '') / filename
        try:
            if wheel.stat().st_size == cached['bytes'] and _file_sha256(wheel) == cached['sha256']:
                print('  Reusing verified ROCm wheel: ' + filename, flush=True)
                return dict(cached)
        except (OSError, KeyError, TypeError):
            pass

    def once():
        digest = hashlib.sha256(); count = 0
        target_dir = cache / ('pending-' + hashlib.sha1(url.encode()).hexdigest())
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'RGBMoCap-Setup/1'}), timeout=300) as response:
            length = response.headers.get('Content-Length', '')
            total = int(length) if length.isdigit() else 0
            print('  Fetching ROCm wheel: %s%s' %
                  (filename, ' (%.0f MiB)' % (total / 2 ** 20) if total else ''), flush=True)
            started = last = time.monotonic()
            with target.open('wb') as stream:
                while True:
                    block = response.read(4 * 1024 * 1024)
                    if not block: break
                    digest.update(block); count += len(block); stream.write(block)
                    now = time.monotonic()
                    if now - last > 5:
                        last = now
                        print('    %s: %.0f/%.0f MiB (%.0f s)' %
                              (filename, count / 2 ** 20, (total or count) / 2 ** 20, now - started), flush=True)
        value = digest.hexdigest()
        print('  Verified ROCm wheel: %s (%.0f MiB)' % (filename, count / 2 ** 20), flush=True)
        folder = cache / value
        folder.mkdir(parents=True, exist_ok=True)
        permanent = folder / filename
        if not permanent.is_file() or permanent.stat().st_size != count:
            os.replace(target, permanent)
        else:
            target.unlink(missing_ok=True)
        name = re.sub(r'[-_.]+', '-', filename.split('-')[0]).lower()
        entry = dict(name=name, version=_version_from_filename(filename), filename=filename,
                     url=url, sha256=value, bytes=count)
        manifest[url] = entry
        partial = manifest_path.with_name(manifest_path.name + '.partial')
        partial.write_text(json.dumps(manifest, indent=2), encoding='utf8')
        os.replace(partial, manifest_path)
        return entry
    return _retry(once, label=filename)


def _version_from_filename(filename):
    parts = filename[:-4].split('-')
    if len(parts) < 2: raise RuntimeError('Unrecognised wheel filename: ' + filename)
    version = parts[1].replace('_', '+')
    return version


def _metadata_requirements(filename, url):
    status, headers, _ = _get(url, {'Range': 'bytes=0-0'})
    size = int(headers['Content-Range'].rsplit('/', 1)[1])
    with zipfile.ZipFile(io.BufferedReader(_RemoteFile(url, size), buffer_size=512 * 1024)) as wheel:
        meta = next(i for i in wheel.infolist() if re.fullmatch(r'[^/]+\.dist-info/METADATA', i.filename))
        parser = HeaderParser().parsestr(wheel.read(meta).decode('utf-8', errors='replace'))
        return parser.get_all('Requires-Dist') or []


def _device_packages(base_name, version, python, platform, arch, cache, entries):
    filename, url = _select(base_name, version, python, platform)
    entries.append(_hash_and_cache(filename, url, cache))
    wanted = f'extra == "device-{arch}"'
    for requirement in _metadata_requirements(filename, url):
        if wanted not in requirement or 'amd-' not in requirement: continue
        # HeaderParser returns the value only (no "Requires-Dist:" prefix).
        name = re.sub(r'[-_.]+', '-', requirement.split()[0].strip()).lower()
        if name in {e['name'] for e in entries}: continue
        pinned = requirement.split('==')[1].split(';')[0].strip()
        dev_file, dev_url = _select(name, pinned, python, platform)
        entries.append(_hash_and_cache(dev_file, dev_url, cache))


def rocm_entries(platform, arch, python_versions, cache):
    """Complete GPU package set for ROCm on one platform, all Python profiles."""
    if platform not in PINS or arch not in _arch_set():
        raise RuntimeError('No ROCm wheel profile for ' + str(platform) + '/' + str(arch) +
                           '. Set RGB_MOCAP_GPU_ARCH to a published gfx architecture.')
    pins = PINS[platform]
    result = {}
    print('Resolving AMD ROCm wheels for %s (%s) from %s' % (arch, platform, AMD_INDEX), flush=True)
    for python in python_versions:
        print('AMD ROCm wheel set for Python %s:' % python, flush=True)
        entries = []
        for name in ('torch', 'torchvision'):
            _device_packages(name, pins[name], python, platform, arch, cache, entries)
        for name, version in COMMON[platform]:
            filename, url = _select(name, version, python, platform)
            entries.append(_hash_and_cache(filename, url, cache))
        for name in ('rocm-bootstrap', 'rocm-sdk-core', 'rocm-sdk-libraries', 'rocm-sdk-device-' + arch):
            version = '0.3.0' if name == 'rocm-bootstrap' else '10.1.0'
            filename, url = _select(name, version, python, platform)
            entries.append(_hash_and_cache(filename, url, cache))
        shim = _bundled_rocm()
        if shim and shim['name'] not in {e['name'] for e in entries}:
            entries.append(shim)
        print('AMD ROCm wheel set for Python %s ready: %d packages.' % (python, len(entries)), flush=True)
        result[python] = entries
    return result


def _arch_set():
    try:
        return {key for key in json.loads((ROOT / 'installer/gpu-arches.json').read_text(encoding='utf-8'))
                if isinstance(key, str) and key.startswith('gfx')}
    except (OSError, ValueError):
        return set()


def _bundled_rocm():
    source = ROOT / 'installer/runtime_sources/rocm-10.1.0-py3-none-any'
    if not source.is_dir(): return None
    filename = 'rocm-10.1.0-py3-none-any.whl'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as wheel:
        for path in sorted(source.rglob('*')):
            if not path.is_file() or path.name == 'SOURCE_WHEEL.json' or '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED; info.external_attr = 0o644 << 16
            wheel.writestr(info, path.read_bytes())
    payload = stream.getvalue()
    return dict(name='rocm', version='10.1.0', filename=filename, url=None,
                bundled='wheels/' + filename, sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload),
                origin='Official AMD source distribution; rebuilt offline from validated pure-Python sources')


def apply_backend(lock, platform, arch, python_versions, cache):
    """Return a lock with GPU packages swapped for the requested backend."""
    entries = rocm_entries(platform, arch, python_versions, cache)
    lock = json.loads(json.dumps(lock))  # deep copy; the caller keeps the pristine lock
    for group in lock['groups']:
        packages = [p for p in group['packages'] if _canonical(p['name']) not in GPU_PACKAGE_NAMES]
        # Overlays inherit Torch/TorchVision from their base runtime; only the
        # two base groups (shared and exmr) need GPU wheels of their own.
        if not group.get('base'):
            packages.extend(entries.get(group['python'], []))
        packages.sort(key=lambda p: _canonical(p['name']))
        group['packages'] = packages
    lock['gpu_backend'] = 'rocm'
    lock['gpu_arch'] = arch
    return lock
