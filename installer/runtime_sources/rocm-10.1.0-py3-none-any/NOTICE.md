# ROCm Python shim (`rocm_sdk`)

This directory contains the pure-Python `rocm_sdk` package and its metadata from
the official AMD `rocm` 10.1.0 source distribution at
`https://stable.repo.amd.com/rocm/whl-next/rocm/rocm-10.1.0.tar.gz`.

AMD publishes this package as a source distribution only. The application
installer rebuilds a deterministic wheel from these exact files (see
`tools/runtime_support.py`) so the private Linux runtime can be installed fully
offline with hash verification. The compiled ROCm libraries come from the
separately pinned, hash-checked `rocm-sdk-*` wheels on the same index.

ROCm is copyright AMD and distributed under its upstream license; no AMD code is
modified here. See https://rocm.docs.amd.com/ for license and support details.
