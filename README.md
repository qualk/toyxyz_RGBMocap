# RGB MoCap - Linux / AMD ROCm fork

> **Fork notice.** This repository is a fork of
> [toyxyz/RGB MoCap](https://github.com/toyxyz/toyxyz_RGBMocap). Its focus is
> Linux and AMD ROCm (HIP) support, with a few QoL fixes and features, but Windows support is still maintained though personally untested. See the
> [upstream README](https://github.com/toyxyz/toyxyz_RGBMocap#readme) for the
> full original feature list.

---

## Tested compatible systems

| Platform | GPU | Backend / target | Notes |
| --- | --- | --- | --- |
| Linux (CachyOS, kernel 7.3.1) | AMD Radeon RX 9060 XT | ROCm 10.1 / HIP 7.16, `gfx1200` | Developer's primary rig |
| Linux (CachyOS, kernel 7.3.1) | AMD Radeon 760M Graphics, ~6.7 GB VRAM | ROCm 10.1 / HIP 7.16, `gfx1102` | ExMR reconstruction ran; first install needed `RGB_MOCAP_GPU_ARCH=gfx1102` |

Windows and other AMD GPUs are supported but untested. If your GPU is older or needed a workaround, or you had
a successful run, or you're encountering any sort of issue, please
[open an issue](https://github.com/qualk/toyxyz_RGBMocap/issues/new) with your
OS, GPU, backend, `gfxNNNN` target and the JSON from **Help > Check System** so
this table can be extended. Avoid opening issues in the main repository unless you know it's not relevant to the fork.

## Install

### Linux

```sh
## Have ROCm setup, refer to docs for more info
git clone https://github.com/qualk/toyxyz_RGBMocap.git
cd toyxyz_RGBMocap
./install.sh
./RGBMoCap.sh
```

If GPU detection cannot identify your card, set `RGB_MOCAP_GPU_BACKEND=rocm` and
`RGB_MOCAP_GPU_ARCH=gfxNNNN` before running the installer.

See the full [Linux guide](docs/LINUX.md) for more detailed instructions and information.

### Windows

```cmd
git clone https://github.com/qualk/toyxyz_RGBMocap.git C:\RGBMoCap
cd /d C:\RGBMoCap
install.cmd
RGBMoCap.cmd
```

Keep the console window open. Use `update.cmd` after closing the app. See the
[Windows install guide](docs/USER_GUIDE.md#install) for prerequisites, disk
space and repair.

## ROCm references

- [AMD ROCm installation (Linux)](https://rocm.docs.amd.com/en/latest/install/rocm.html)
- [Radeon / Ryzen ROCm guide](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/index.html)
- [HIP SDK for Windows](https://rocm.docs.amd.com/projects/install-on-windows/en/latest/install/install.html)
- [TheRock releases and GPU device-target lookup](https://github.com/ROCm/TheRock/blob/main/RELEASES.md)
- [ROCm GPU support status](https://github.com/ROCm/TheRock/blob/main/SUPPORTED_GPUS.md)

The installer manages its own pinned, hash-checked ROCm wheels; these links are
for AMD driver/runtime prerequisites and for finding your `gfxNNNN` target.
Check **Help > Check System** after setup to confirm a working device.

## Documentation

- [Linux setup and GPU compatibility](docs/LINUX.md)
- [User guide: controls, models and settings](docs/USER_GUIDE.md)
- [Model guides](docs/models/README.md)
- [Agent guide](docs/AGENT_GUIDE.md)
- [Source validation](docs/VALIDATION.md)

Original RGB MoCap code and documentation use the [MIT License](LICENSE).
Third-party components and model files retain their own licenses.
