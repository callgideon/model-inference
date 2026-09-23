"""bench.py --sampler for the pilot box: host load and GPU memory/utilisation (nvidia-smi).
A failed read leaves the series absent for that sample - never a made-up value."""
import os
import subprocess


def make():
    def sample():
        out = {}
        try:
            out["load1"] = round(os.getloadavg()[0], 2)
        except OSError:
            pass
        try:
            used, util = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5).stdout.split(",")[:2]
            out["gpu_mem_mib"], out["gpu_util_pct"] = float(used), float(util)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return out
    return sample
