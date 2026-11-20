from __future__ import annotations

import platform


def collect() -> dict:
    import psutil
    result = {"platform": platform.platform(), "python": platform.python_version(),
              "ram_gib": round(psutil.virtual_memory().total / 1024**3, 1), "cuda": False,
              "gpu_backend": "none", "rocm": False}
    try:
        import torch
        result["torch"] = torch.__version__
        # ROCm builds expose torch.cuda but set version.hip instead of version.cuda.
        hip = getattr(torch.version, 'hip', None)
        result["gpu_backend"] = "rocm" if hip else "cuda"
        result["rocm"] = bool(hip)
        result["cuda_runtime"] = torch.version.cuda or hip
        if hip: result["hip_runtime"] = hip
        result["cuda"] = torch.cuda.is_available()
        if result["cuda"]:
            result["gpu"] = torch.cuda.get_device_name(0)
            result["vram_gib"] = round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1)
            result["compute_capability"] = list(torch.cuda.get_device_capability(0))
            props = torch.cuda.get_device_properties(0)
            if getattr(props, 'gcnArchName', None): result["gpu_arch"] = props.gcnArchName
            x = torch.arange(4096, device="cuda", dtype=torch.float32).reshape(64, 64)
            value = (x @ x.T).sum().item()
            result["cuda_matmul_verified"] = value > 0
            # TorchVision keeps compiled ops (NMS, ROI align) in a separate
            # architecture-specific kernel package on ROCm. A plain matmul can
            # pass while person detection fails with hipErrorInvalidDeviceFunction,
            # so exercise NMS explicitly here.
            try:
                import torchvision
                boxes = torch.tensor([[0., 0., 10., 10.], [1., 1., 11., 11.]], device="cuda")
                scores = torch.tensor([0.9, 0.8], device="cuda")
                torchvision.ops.nms(boxes, scores, 0.5)
                result["torchvision_nms_verified"] = True
            except Exception as nms_error:
                result["torchvision_nms_verified"] = False
                result["torchvision_nms_error"] = str(nms_error)[:300]
            torch.cuda.synchronize()
    except Exception as error:
        result["error"] = str(error)
    return result
