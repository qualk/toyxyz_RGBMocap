# Linux

This fork runs single-video ExMR, FlowHMR and GVHMR on Linux, including motion
correction, source export and optional Track Face. The optional PEAR, MAMMA,
PromptHMR, GEM-X, SAM 3.1 and Unreal Live Link setups still use Windows tooling.

Both platforms share the same pinned runtime profiles. Setup detects an NVIDIA
(CUDA) or AMD (ROCm/HIP) GPU and downloads matching hash-checked wheels, so
there is no system PyTorch to install. AMD users should read the ROCm section
before the first install.

## 1. Install system packages

You need x86-64 Linux, OpenGL 4.3 or newer, FFmpeg/FFprobe and a C++17 compiler
with CMake 3.24 or newer. The commands below install the usual Qt, OpenGL and
build dependencies. `portaudio` is only needed for Track Face. Package names
change between releases, so drop anything your distro cannot find.

### Arch Linux / CachyOS / EndeavourOS

```sh
sudo pacman -Syu --needed git curl ca-certificates base-devel cmake ffmpeg \
  mesa-utils glu libglvnd libxkbcommon-x11 xcb-util-cursor xcb-util-wm \
  xcb-util-image xcb-util-keysyms xcb-util-renderutil libxcb portaudio
```

`--needed` skips packages you already have, so rerunning the command is safe.
`libxcb` already contains the RandR, shape, Xfixes and Xinerama libraries.

### Ubuntu / Debian / Linux Mint

```sh
sudo apt update
sudo apt install git curl ca-certificates build-essential cmake ffmpeg mesa-utils \
  libgl1 libegl1 libglu1-mesa libxkbcommon-x11-0 libxcb-cursor0 \
  libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
  libxcb-randr0 libxcb-shape0 libxcb-xfixes0 libxcb-xinerama0 libportaudio2
```

Ubuntu 24.04 and Linux Mint 22 are the distributions closest to the upstream
tested setup.

### Fedora

```sh
sudo dnf install git curl ca-certificates gcc gcc-c++ make cmake ffmpeg-free \
  glx-utils mesa-libGL mesa-libEGL mesa-libGLU libxkbcommon-x11 xcb-util-cursor \
  xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil libxcb portaudio
```

Fedora ships `ffmpeg-free` in its normal repositories; enable RPM Fusion if you
want the full `ffmpeg` package instead.

### openSUSE

```sh
sudo zypper install git curl ca-certificates gcc gcc-c++ make cmake ffmpeg \
  Mesa-libGL1 Mesa-libEGL1 Mesa-libGLU1 libxkbcommon-x11-0 xcb-util-cursor \
  xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil libxcb1 portaudio
```

If `ffmpeg` is missing from the default repositories, enable the Packman
repository. `glxinfo` comes from `mesa-utils` on Arch and Debian, `glx-utils` on Fedora and
`Mesa-demo-utils` on openSUSE. It is only useful for display troubleshooting.

### Blender (optional)

Blender project export, compatibility FBX and character retargeting need
Blender 4.5 LTS. `./install.sh --with-blender` downloads and verifies the pinned
build. You can also use Blender from `PATH` or set
`RGBMOCAP_BLENDER=/path/to/blender`. On Ubuntu 24.04 also install `libsm6
libice6`; on Arch use `libsm libice`.

## 2. AMD GPU setup (ROCm)

Skip this section for NVIDIA.

Your kernel already provides the `amdgpu` driver and the installer downloads its
own ROCm wheels, so the full AMD ROCm SDK is not required. You do need GPU
device permissions and a way for setup to identify the card.

```sh
sudo usermod -aG render,video "$USER"
```

Log out and back in (or reboot) so the new groups apply, then check:

```sh
groups | grep -E 'render|video'
rocminfo | grep -m1 -i gfx
```

If `rocminfo` is missing, install it with `sudo pacman -S --needed rocminfo
pciutils` on Arch/CachyOS or `sudo apt install pciutils` on Ubuntu/Debian
(`rocminfo` comes from AMD's ROCm repository if your distro does not package
it). Setup falls back to `lspci` and `/sys/class/drm`, but `rocminfo` gives the
most reliable architecture name.

### If the GPU is not detected

Setup prints the detected backend and `gfx` target. If the card is unknown, or
the download reports no matching wheel build, set them explicitly:

```sh
export RGB_MOCAP_GPU_BACKEND=rocm
export RGB_MOCAP_GPU_ARCH=gfxNNNN
./install.sh
```

The variables persist for later runs in the same shell and
`RGB_MOCAP_GPU_ARCH` always overrides detection. Use the target from the table
below. Integrated Radeon cards (760M/780M) are listed as `gfx1103`, but AMD does
not always publish wheels for every target; the 760M test for this fork used
`gfx1102`. Try the nearest supported target if the exact one fails.

## 3. Install, launch and update

```sh
git clone https://github.com/qualk/toyxyz_RGBMocap.git
cd toyxyz_RGBMocap
./install.sh        # first setup, downloads several GB
./RGBMoCap.sh       # start the app
./update.sh         # later: update source and verify runtimes
```

Keep the terminal open during setup. It prints what it is doing, retries failed
downloads when rerun, and finishes with a `SETUP_RECEIPT.json` file at the
repository root. Do not start the app from another terminal while setup is
running.

| Command | What it does |
| --- | --- |
| `./install.sh --verify-all` | Recheck every installed runtime file and import |
| `./install.sh --with-blender` | Also install the pinned Blender 4.5 |

After setup, use **Model Manager** in the app to download or point at model
files. Models are not bundled and some require registration.

If your filesystem runs out of metadata space, put setup scratch files on
another disk:

```sh
mkdir -p /tmp/rgbmocap-setup
RGB_MOCAP_SETUP_TEMP=/tmp/rgbmocap-setup ./install.sh
```

Setup keeps its own files under `.setup/` and private runtimes under
`runtime*/`; models, projects and caches stay wherever you save them.

## Wayland

The launcher uses Qt's XCB backend through XWayland when `DISPLAY` is set, which
avoids Qt OpenGL failures on some Wayland desktops. An explicit
`QT_QPA_PLATFORM` always wins:

```sh
QT_QPA_PLATFORM=wayland ./RGBMoCap.sh
```

Native Wayland needs your distro's Qt Wayland plugin (`qt6-wayland` on Arch).
If the viewport is black or software-rendered, run `glxinfo -B` and check that
it reports your GPU instead of `llvmpipe`.

## WSL 2

Use a checkout on the Linux filesystem (for example `~/toyxyz_RGBMocap`), not
under `/mnt/c`, and keep the NVIDIA Windows driver installed; WSL exposes it to
Ubuntu. AMD ROCm under WSL is not supported here, so use native Linux for AMD
cards.

If Qt falls back to software rendering under WSLg:

```sh
QT_QPA_PLATFORM=xcb GALLIUM_DRIVER=d3d12 MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA ./RGBMoCap.sh
```

## Supported AMD architectures

Setup queries AMD's wheel index for the detected platform and `gfx` target, so a
card usually works as soon as AMD publishes matching wheels.
`installer/gpu-arches.json` maps marketing names from `rocminfo` or `lspci` to
targets; add an entry there for a new card instead of changing code.

| `gfx` target | Cards |
| --- | --- |
| `gfx1200` | RX 9060 XT / 9060 / 9050, Radeon AI PRO R9600 |
| `gfx1201` | RX 9070 XT / 9070, Radeon AI PRO R9700 |
| `gfx1100` | RX 7900 XTX / XT / GRE, Radeon PRO W7900 |
| `gfx1101` | RX 7800 XT / 7700 XT |
| `gfx1102` | RX 7600 / 7500, Radeon PRO W7700 |
| `gfx1103` | Radeon 780M / 760M / 740M (integrated) |
| `gfx1150` | Radeon 880M / 890M (Ryzen AI 9) |
| `gfx1151` | Radeon 8060S / 8050S (Ryzen AI Max) |
| `gfx1030` | RX 6800 / 6800 XT / 6900 XT |
| `gfx1031` | RX 6700 / 6750 XT |
| `gfx1032` | RX 6600 / 6650 XT |

Only `gfx1200` is hardware-validated here. Confirm your backend, GPU, HIP
version and device test in **Help > Check System** after setup. For AMD driver
and device-target references, see the [README ROCm links](../README.md#rocm-references).
Older cards can work with workarounds but are not officially supported; open an
issue if you need help.