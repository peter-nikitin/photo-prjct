"""Read-only Linux host and worker-cgroup sample; no credentials or process arguments."""

import json
import os
import time
from pathlib import Path


def pairs(path):
    return {
        r.split()[0].rstrip(":"): int(r.split()[1])
        for r in Path(path).read_text().splitlines()
        if len(r.split()) > 1 and r.split()[1].isdigit()
    }


def sample():
    cgroup_name = Path("/proc/1/cgroup").read_text().strip().split("::", 1)[1]
    cgroup = Path("/sys/fs/cgroup") / cgroup_name.lstrip("/")
    # Sum resident pages over every process visible in this worker PID namespace,
    # including inference child and server. cgroup peak additionally bounds missed peaks.
    rss = 0
    for p in Path("/proc").glob("[0-9]*/statm"):
        if p.parent.name == str(os.getpid()):
            continue
        try:
            rss += int(p.read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            pass
    mem = pairs("/proc/meminfo")
    vm = pairs("/proc/vmstat")
    print(
        json.dumps(
            {
                "at": time.time(),
                "rss_bytes": rss,
                "cgroup_peak_bytes": int((cgroup / "memory.peak").read_text()),
                "memory_limit_bytes": int((cgroup / "memory.max").read_text()),
                "oom_kills": pairs(cgroup / "memory.events")["oom_kill"],
                "worker_cpu_usec": pairs(cgroup / "cpu.stat")["usage_usec"],
                "host_cpu_ticks": list(
                    map(int, Path("/proc/stat").read_text().splitlines()[0].split()[1:9])
                ),
                "host_available_bytes": mem["MemAvailable"] * 1024,
                "swap_in_pages": vm["pswpin"],
                "swap_out_pages": vm["pswpout"],
                "disk_available_bytes": os.statvfs("/tmp").f_bavail * os.statvfs("/tmp").f_frsize,
            }
        ),
        flush=True,
    )


while True:
    sample()
    time.sleep(2)
