# cgmon

`cgmon` is a highly specialized, `vmstat`/`pidstat`-like CLI tool designed for Site Reliability Engineers (SREs). It monitors Linux **cgroups v2** metrics (CPU, Memory, I/O, PIDs, and PSI) in real-time, providing immediate visibility into container throttling and resource exhaustion.

## Motivation

In modern containerized environments (like Kubernetes and Docker), your application's `resources.limits` are translated directly into underlying Linux cgroup restrictions. When containers experience mysterious latency spikes or sudden `OOMKilled` crashes, the answers are often hidden deep within `/sys/fs/cgroup/`. 

`cgmon` bridges the gap. Instead of forcing you to manually `cat` dozens of files to troubleshoot, it streams the raw, critical kernel metrics in a continuous, easily-digestible, and color-coded columnar format that perfectly mimics `pidstat`.

## Features

- **Smart Resolution**: Target a specific process by its PID (`-p`), and `cgmon` will automatically resolve and monitor its underlying cgroup.
- **Dynamic Modular Columns**: Select exactly which metrics you want to monitor (`-m cpu,memory,io,pids`).
- **Human-Readable Parsing**: Automatically converts raw microsecond counters and byte sizes into readable units (e.g., `10ms`, `1.5G`).
- **PSI Integration**: Seamlessly embeds modern Pressure Stall Information (`*.pressure`) metrics into the CPU, Memory, and I/O outputs.
- **`pidstat` Visuals**: Utilizes alternating ANSI colors for high terminal readability when tracking dense rows of data.

## Prerequisites

- A Linux environment with **cgroups v2** mounted (default on modern distributions).
- Python 3.6+

## Installation & Quickstart

Since `cgmon` is built with Python standard libraries, it requires absolutely zero dependencies!

```bash
# 1. Download the tool
git clone https://github.com/benkuotw/cgmon.git
cd cgmon

# 2. (Optional) Install system-wide
sudo cp cgmon cgmon.py /usr/local/bin/

# 3. Monitor your current bash session's cgroup (default: cpu, memory)
./cgmon -p $$ 1
```

## Usage

```bash
./cgmon [-p PID] [-c CGROUP_PATH] [-m CONTROLLERS] [interval] [count]
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `-p PID` | Automatically resolve the cgroup path from `/proc/<PID>/cgroup`. | None |
| `-c PATH` | Directly monitor a specific cgroup (e.g., `kubepods.slice/pod-123`). | `/sys/fs/cgroup` |
| `-m MODULES`| Comma-separated list of controllers to display (`cpu`, `memory`, `io`, `pids`). | `cpu,memory` |
| `interval` | Delay between updates in seconds. | `1` |
| `count` | Number of updates to display before exiting. | Infinite |

### Examples

**Monitor CPU and I/O for a specific process every 2 seconds:**
```bash
./cgmon -p 1042 -m cpu,io 2
```

**Monitor all metrics for a Kubernetes pod:**
```bash
./cgmon -c kubepods.slice/kubepods-burstable-pod123.slice -m cpu,memory,io,pids 1
```

## Testing with the Simulator

This repository includes a `simulate_prod.py` script that artificially generates heavy CPU, Memory, and I/O load. It's the perfect way to see `cgmon` in action.

You can use `systemd-run` to impose artificial cgroup limits on the simulator, and then watch `cgmon` report the throttling and OOM events!

```bash
# 1. Start the simulator with a 50% CPU limit and 150MB memory limit
systemd-run --user --scope -p CPUQuota=50% -p MemoryMax=150M python3 simulate_prod.py

# 2. In a second terminal, monitor the PID it outputs
./cgmon -p <PID> -m cpu,memory,io,pids 1
```

## Critical Metric Definitions

*   **`nr_thr` / `thr_us` (CPU)**: Number of times and total duration the process was throttled by the kernel for exceeding its CPU limit. A high number here explains random latency spikes.
*   **`anon` (Memory)**: Anonymous memory (heap/stack). This is the true footprint of your application.
*   **`mjflt` (Memory)**: Major page faults. Rapid climbing means your application is thrashing disk/swap.
*   **`psi` (All)**: Pressure Stall Information. The total time processes in the cgroup were completely stalled waiting for CPU, Memory, or I/O.
*   **`oom` (Memory)**: The exact number of times the kernel's OOM killer terminated a process in this cgroup.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
