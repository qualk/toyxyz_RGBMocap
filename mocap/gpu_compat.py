"""Linux/ROCm runtime compatibility shims applied before Torch is imported.

ROCm's PyTorch preloads its own OpenBLAS into the global dynamic-linker
namespace. That build is LP64 and selects AVX-512 kernels; NumPy's bundled
OpenBLAS is ILP64. When both are loaded, NumPy's ``cblas_dgemm64_`` resolves
``dgemm_tn`` to the ROCm library and passes 64-bit dimensions to a 32-bit ABI,
which segfaults on Zen 4 CPUs (no AVX-512). Loading NumPy's own OpenBLAS into
the global namespace first makes symbol resolution pick the matching ILP64
implementation. This is a CPU-only issue; it does not change inference results,
precision, batch sizes or temporal context.
"""
from __future__ import annotations

import os
import sysconfig
from pathlib import Path


def install_rocm_blas_compat():
    """Preload NumPy's ILP64 OpenBLAS for ROCm Linux workers; no-op elsewhere."""
    if os.name == 'nt':
        return
    site = Path(sysconfig.get_paths()['purelib'])
    rocm_libs = site / '_rocm_sdk_core' / 'lib' / 'host-math' / 'lib'
    if not any(rocm_libs.glob('librocm-openblas.so*')):
        return
    numpy_libs = site / 'numpy.libs'
    if not numpy_libs.is_dir():
        return
    import ctypes
    # Dependency order: quadmath, then gfortran, then OpenBLAS itself.
    for pattern in ('libquadmath-*.so*', 'libgfortran-*.so*', 'libopenblas64_p-*.so'):
        for path in sorted(numpy_libs.glob(pattern)):
            try:
                ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass
