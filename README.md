# cgmon

`cgmon` is a highly specialized, `pidstat`-like CLI tool designed for Site Reliability Engineers (SREs). It monitors Linux **cgroups v2** metrics (CPU, Memory, I/O, PIDs, and PSI) in real-time, providing immediate visibility into container throttling and resource exhaustion.

## Motivation

In modern containerized environments (like Kubernetes and Docker), your application's `resources.limits` are translated directly into underlying Linux cgroup restrictions. When containers experience mysterious latency spikes or sudden `OOMKilled` crashes, the answers are often hidden deep within `/sys/fs/cgroup/`. 

`cgmon` bridges the gap. Instead of forcing you to manually `cat` dozens of files to troubleshoot, it streams the critical kernel metrics in a continuous, color-coded columnar format modeled on `pidstat`.

## Features

- **Smart Resolution**: Target a specific process by its PID (`-p`), and `cgmon` will automatically resolve and monitor its underlying cgroup.
- **Interval Deltas**: CPU usage is shown as a percentage of one core (like `pidstat`'s `%CPU`); other cumulative counters (throttling, OOM kills, PSI, I/O) show what happened *during the interval* rather than since boot.
- **Honest Gaps**: A value whose kernel file is missing (e.g. the `io` controller is not enabled for the cgroup) is shown as `-`, never as a fake `0`, and `cgmon` warns about disabled controllers at startup.
- **Dynamic Modular Columns**: Select exactly which metrics you want to monitor (`-m cpu,memory,io,pids`).
- **Human-Readable Parsing**: Automatically converts raw microsecond counters and byte sizes into readable units (e.g., `10ms`, `1.5G`).
- **PSI Integration**: Seamlessly embeds modern Pressure Stall Information (`*.pressure`) metrics into the CPU, Memory, and I/O outputs.
- **`pidstat` Visuals**: Utilizes alternating ANSI colors for high terminal readability when tracking dense rows of data.

## Prerequisites

- A Linux environment with **cgroups v2** mounted (default on modern distributions).
- Python 3.9+

## Installation & Quickstart

Since `cgmon` is built with Python standard libraries, it requires absolutely zero dependencies!

```bash
# 1. Download the tool
git clone https://github.com/benkuotw/cgmon.git
cd cgmon

# 2. (Optional) Install system-wide
sudo mkdir -p /usr/local/lib/cgmon
sudo cp cgmon cgmon.py /usr/local/lib/cgmon/
sudo ln -sf /usr/local/lib/cgmon/cgmon /usr/local/bin/cgmon

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
| `-c PATH` | Directly monitor a specific cgroup, relative to `/sys/fs/cgroup` (e.g., `kubepods.slice/pod-123`) or absolute. | `/sys/fs/cgroup` (root cgroup; many files such as `memory.current` do not exist there and show `-`) |
| `-m MODULES`| Comma-separated list of controllers to display (`cpu`, `memory`, `io`, `pids`). Unknown names are an error. | `cpu,memory` |
| `interval` | Delay between updates in seconds. | `1` |
| `count` | Number of updates to display before exiting. | Infinite |

### Examples

**Monitor CPU and I/O for a specific process every 2 seconds:**
```bash
./cgmon -p 1234 -m cpu,io 2
```

**Monitor all metrics for a Kubernetes pod:**
```bash
./cgmon -c kubepods.slice/kubepods-burstable-pod123.slice -m cpu,memory,io,pids 1
```

## Testing with the Simulator

This repository includes a `simulate_prod.py` script that artificially generates heavy CPU, Memory, and I/O load. It's the perfect way to see `cgmon` in action.

You can use `systemd-run` to impose artificial cgroup limits on the simulator, and then watch `cgmon` report the throttling and OOM events!

```bash
# 1. Start the simulator with a 50% CPU limit and 150MB memory limit (no swap, so the OOM killer fires)
systemd-run --user --scope -p CPUQuota=50% -p MemoryMax=150M -p MemorySwapMax=0 python3 simulate_prod.py

# 2. In a second terminal, monitor the PID it outputs
./cgmon -p <PID> -m cpu,memory,io,pids 1
```

`--user` scopes only get the controllers systemd delegates to your user manager. Check `cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers`: if `cpu` is missing, `CPUQuota` is silently ignored (drop `--user` and run with `sudo` instead). `io` is usually not delegated, so the `io` columns show `-` for user scopes; the Docker demo below has all controllers.

### Running the Simulator in Docker

`demo.sh` runs the simulator in a resource-limited container and points `cgmon` at it. Run it on the Docker host itself (a Linux machine or VM with cgroups v2); `cgmon` reads the host's `/sys/fs/cgroup` directly and does not need root.

```bash
./demo.sh build        # build the cgmon-sim image
./demo.sh up           # start the container (0.5 CPU, 150M memory, 64 pids)
./demo.sh observe 1    # run cgmon against the container every 1s (Ctrl-C to stop)
./demo.sh logs         # follow the simulator's output (OOM kills, respawns)
./demo.sh down         # remove the container; a running cgmon exits cleanly
```

The container is locked down: the simulator runs as an unprivileged user (uid 10001) against root-owned code, with `--cap-drop=ALL` and `--security-opt=no-new-privileges`. `--memory-swap` equals `--memory`, so memory pressure ends in OOM kills rather than swapping.

`observe` resolves the container through its host PID (`docker inspect -f '{{.State.Pid}}'`). You can also target the cgroup directly; with Docker's systemd cgroup driver it is:

```bash
./cgmon -c system.slice/docker-$(docker inspect -f '{{.Id}}' cgmon-sim).scope -m cpu,memory,io,pids 1
```

## Running the Tests

```bash
python3 -m unittest discover -s tests -v
```

## Critical Metric Definitions

*(Note: Unless marked as a gauge or a percentage, `cgmon` displays the **delta** of these metrics over the polling interval, so `cgmon 2` shows roughly twice the values of `cgmon 1`. A `-` means the value is unavailable for this cgroup.)*

*   **`%cpu` / `%usr` / `%sys` (CPU)**: [Percentage] CPU time used by the whole cgroup divided by wall time, like `pidstat`'s `%CPU`. `100.0` is one full core; a cgroup limited by `max` = `0.5c` tops out around `50.0`.
*   **`nr_thr` / `thr%` / `thr_us` (CPU)**: Number of CFS periods in which the cgroup was throttled, that number as a percentage of the elapsed periods, and the total time its tasks spent throttled, all during the interval. A high value here explains random latency spikes.
*   **`max` (CPU)**: [Gauge] The `cpu.max` quota in cores (`0.5c`), or `max` if unlimited.
*   **`anon` (Memory)**: [Gauge] Anonymous memory (heap/stack). This is the true instantaneous footprint of your application.
*   **`mjflt` (Memory)**: Major page faults occurring during the interval. Rapid climbing means your application is thrashing disk/swap.
*   **`psi` (CPU, Memory, I/O)**: Pressure Stall Information, the `some` line: the time during which *at least one* task in the cgroup was stalled waiting for CPU, memory, or I/O during the interval. (`full`, where all non-idle tasks were stalled at once, is not shown.)
*   **`oom` (Memory)**: The number of processes the kernel's OOM killer terminated in this cgroup *or its descendants* during the interval (`memory.events` is hierarchical unless cgroup2 is mounted with `memory_localevents`).

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
