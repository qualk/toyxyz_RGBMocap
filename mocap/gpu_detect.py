"""Dependency-free GPU vendor/architecture detection for setup and diagnostics.

Setup runs under a private CPython before any Torch is installed, so detection
uses the platform's own tools (rocminfo, lspci, PowerShell/WMI) plus a small
name-to-architecture table in installer/gpu-arches.json. Explicit environment
overrides always win, which also lets a user select the right ROCm kernels for
a card whose marketing name is not in the table yet.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCH_TABLE = ROOT / 'installer' / 'gpu-arches.json'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def _run(command, timeout=12):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              check=False, creationflags=NO_WINDOW)
        return done.stdout if done.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def _table():
    try:
        return json.loads(ARCH_TABLE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def arch_from_name(name, vendor=None):
    """Match a GPU marketing name against the data-driven arch table."""
    text = (name or '').lower()
    if not text:
        return None
    for arch, tokens in _table().items():
        if not arch.startswith('gfx') or not isinstance(tokens, list):
            continue
        if vendor and vendor == 'nvidia':
            continue
        if any(str(token).lower() in text for token in tokens):
            return arch
    return None


def _linux_gpu():
    """Return (name, arch, vendor) using rocminfo, lspci or sysfs."""
    rocm = shutil.which('rocminfo') or '/opt/rocm/bin/rocminfo'
    if os.path.isfile(rocm):
        output = _run([rocm])
        block = ''
        for line in output.splitlines():
            if line.strip() == '*******':
                if 'Device Type: GPU' in block and 'Vendor Name: AMD' in block:
                    found = re.search(r'Name:\s*(gfx[0-9a-fA-F]+)', block)
                    marketing = re.search(r'Marketing Name:\s*(.+)', block)
                    if found:
                        return (marketing.group(1).strip() if marketing else found.group(1),
                                found.group(1).lower(), 'amd')
                block = ''
            else:
                block += line + '\n'
    lpci = _run(['lspci']) if shutil.which('lspci') else ''
    for line in lpci.splitlines():
        low = line.lower()
        if 'vga compatible controller' not in low and '3d controller' not in low:
            continue
        if 'nvidia' in low:
            return (line.split(': ', 1)[-1], None, 'nvidia')
        if 'amd' in low or 'ati' in low:
            name = line.split(': ', 1)[-1]
            return (name, arch_from_name(name, 'amd'), 'amd')
    for card in sorted(Path('/sys/class/drm').glob('card[0-9]')):
        vendor = (card / 'device' / 'vendor').read_text().strip() if (card / 'device' / 'vendor').is_file() else ''
        if vendor == '0x10de':
            return ('NVIDIA GPU', None, 'nvidia')
        if vendor == '0x1002':
            return ('AMD GPU', None, 'amd')
    return ('', None, None)


def _windows_gpu():
    name = ''
    ps = shutil.which('powershell') or shutil.which('powershell.exe')
    if ps:
        name = _run([ps, '-NoProfile', '-Command',
                     '(Get-CimInstance Win32_VideoController).Name -join ";"'])
    if not name.strip():
        name = _run(['wmic', 'path', 'win32_VideoController', 'get', 'name'])
    name = name.strip().splitlines()
    names = [line.strip() for line in name if line.strip() and 'name' not in line.lower()]
    text = ';'.join(names)
    low = text.lower()
    if 'nvidia' in low:
        return (text, None, 'nvidia')
    if 'amd' in low or 'radeon' in low:
        return (text, arch_from_name(text, 'amd'), 'amd')
    return (text, None, None)


def detect():
    """Return {'name','vendor','backend','arch'} for the primary GPU."""
    name, arch, vendor = _windows_gpu() if os.name == 'nt' else _linux_gpu()
    requested = os.environ.get('RGB_MOCAP_GPU_BACKEND', 'auto').strip().lower()
    if requested not in ('auto', 'cuda', 'rocm'):
        raise RuntimeError('RGB_MOCAP_GPU_BACKEND must be auto, cuda or rocm.')
    backend = requested if requested != 'auto' else ('rocm' if vendor == 'amd' else 'cuda' if vendor == 'nvidia' else '')
    arch = os.environ.get('RGB_MOCAP_GPU_ARCH', '').strip().lower() or arch
    return dict(name=name, vendor=vendor, backend=backend, arch=arch)
