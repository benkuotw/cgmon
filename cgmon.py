#!/usr/bin/env python3
import argparse
import datetime
import os
import shutil
import signal
import sys
import time

CGROUP_ROOT = "/sys/fs/cgroup"

# Shown in place of a value whose source file or field does not exist, so it is
# never confused with a real zero (e.g. io.stat when the io controller is not enabled)
MISSING = "-"

# Formatting helpers: each accepts a raw string, a number, or None (missing)
def format_usec(usec_str):
    if usec_str is None:
        return MISSING
    try:
        usec = int(usec_str)
    except ValueError:
        return str(usec_str)
    if usec < 1000:
        return f"{usec}us"
    elif usec < 1_000_000:
        ms = usec / 1000
        return f"{ms:.1f}ms".replace(".0ms", "ms") if ms < 10 else f"{int(ms)}ms"
    elif usec < 100_000_000:
        return f"{usec/1_000_000:.1f}s"
    else:
        return f"{usec // 1_000_000}s"

def format_bytes(bytes_str):
    if bytes_str is None:
        return MISSING
    try:
        b = int(bytes_str)
    except ValueError:
        return str(bytes_str)
    if b < 1024:
        return f"{b}B"
    elif b < 1024**2:
        return f"{b/1024:.1f}K".replace('.0K', 'K')
    elif b < 1024**3:
        return f"{b/(1024**2):.1f}M".replace('.0M', 'M')
    elif b < 1024**4:
        return f"{b/(1024**3):.1f}G".replace('.0G', 'G')
    else:
        return f"{b/(1024**4):.1f}T".replace('.0T', 'T')

def format_count(count_str):
    if count_str is None:
        return MISSING
    try:
        c = int(count_str)
    except ValueError:
        return str(count_str)
    if c < 1000:
        return str(c)
    elif c < 1_000_000:
        return f"{c/1000:.1f}K".replace('.0K', 'K')
    else:
        return f"{c/1_000_000:.1f}M".replace('.0M', 'M')

def format_pct(val):
    if val is None:
        return MISSING
    return f"{float(val):.1f}"

def format_cpu_max(val_str):
    return MISSING if val_str is None else str(val_str)

_FILE_CACHE = {}

def read_cgroup_file(cg_path, filename):
    """Return the stripped file content, or None if the file cannot be read."""
    filepath = os.path.join(cg_path, filename)
    try:
        if filepath not in _FILE_CACHE:
            _FILE_CACHE[filepath] = open(filepath, 'r')
        f = _FILE_CACHE[filepath]
        f.seek(0)
        return f.read().strip()
    except OSError:
        # Evict the handle so a deleted-and-recreated cgroup is reopened next tick
        f = _FILE_CACHE.pop(filepath, None)
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
        return None

def close_file_cache():
    for f in _FILE_CACHE.values():
        try:
            f.close()
        except OSError:
            pass
    _FILE_CACHE.clear()

def parse_kv(content):
    if content is None:
        return None
    res = {}
    for line in content.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            res[parts[0]] = parts[1]
    return res

def parse_cpu_max(content):
    if content is None:
        return None
    parts = content.split()
    if not parts or parts[0] == "max":
        return "max"
    try:
        quota = int(parts[0])
        period = int(parts[1]) if len(parts) > 1 else 100000
        if period <= 0:
            return parts[0]
        cores = quota / period
        if cores == int(cores):
            return f"{int(cores)}c"
        return f"{cores:.1f}c"
    except ValueError:
        return parts[0]

def parse_pressure(content):
    """Return the cumulative 'some' stall time (usec) from a *.pressure file."""
    if content is None:
        return None
    for line in content.splitlines():
        if line.startswith("some"):
            for p in line.split():
                if p.startswith("total="):
                    return p.split("=", 1)[1]
    return None

def parse_io_stat(content):
    if content is None:
        return None
    # An empty io.stat is valid: it means no I/O has happened yet
    total = {'rbytes': 0, 'wbytes': 0, 'rios': 0, 'wios': 0}
    for line in content.splitlines():
        for p in line.split()[1:]:
            k, _, v = p.partition("=")
            if k in total:
                try:
                    total[k] += int(v)
                except ValueError:
                    pass
    return total

def parse_io_weight(content):
    """Return the default weight from io.weight ("default N" or a bare "N")."""
    if not content:
        return None
    for line in content.splitlines():
        parts = line.split()
        if parts and parts[0] == "default" and len(parts) >= 2:
            return parts[1]
    parts = content.split()
    return parts[1] if len(parts) >= 2 else parts[0]

def parse_proc_cgroup(content):
    """Return the cgroups v2 path (without leading '/') from /proc/<pid>/cgroup, or None."""
    for line in content.splitlines():
        if line.startswith("0::"):
            # maxsplit keeps paths that themselves contain ':' intact
            return line.strip().split(":", 2)[2].lstrip("/")
    return None

def resolve_pid_cgroup(pid):
    """Return the cgroup directory of pid, or None if it has no cgroups v2 entry. Raises OSError."""
    with open(f"/proc/{pid}/cgroup", "r") as f:
        suffix = parse_proc_cgroup(f.read())
    if suffix is None:
        return None
    return os.path.normpath(os.path.join(CGROUP_ROOT, suffix))

COL_DEF = {
    'cpu': [
        ('%cpu', 6, format_pct), ('%usr', 6, format_pct), ('%sys', 6, format_pct),
        ('nr_thr', 6, format_count), ('thr%', 5, format_pct), ('thr_us', 7, format_usec),
        ('max', 6, format_cpu_max), ('psi', 7, format_usec)
    ],
    'memory': [
        ('cur', 7, format_bytes), ('anon', 7, format_bytes), ('file', 7, format_bytes),
        ('mjflt', 6, format_count), ('oom', 5, format_count),
        ('max', 7, format_bytes), ('psi', 7, format_usec)
    ],
    'io': [
        ('rbytes', 8, format_bytes), ('wbytes', 8, format_bytes),
        ('rios', 7, format_count), ('wios', 7, format_count),
        ('weight', 6, format_count), ('psi', 7, format_usec)
    ],
    'pids': [
        ('cur', 6, format_count), ('max', 6, format_count)
    ]
}

# Columns backed by cumulative kernel counters; shown as the change over the interval
CUMULATIVE = {'nr_thr', 'thr_us', 'psi', 'mjflt', 'oom', 'rbytes', 'wbytes', 'rios', 'wios'}

def _get(d, key):
    return None if d is None else d.get(key)

def collect_data(cg_path, modules):
    """Snapshot the raw counters and gauges; a value is None when its source is missing."""
    data = {}
    if 'cpu' in modules:
        cpu_stat = parse_kv(read_cgroup_file(cg_path, "cpu.stat"))
        data['cpu'] = {
            'usage': _get(cpu_stat, 'usage_usec'),
            'usr': _get(cpu_stat, 'user_usec'),
            'sys': _get(cpu_stat, 'system_usec'),
            'nr_periods': _get(cpu_stat, 'nr_periods'),
            'nr_thr': _get(cpu_stat, 'nr_throttled'),
            'thr_us': _get(cpu_stat, 'throttled_usec'),
            'max': parse_cpu_max(read_cgroup_file(cg_path, "cpu.max")),
            'psi': parse_pressure(read_cgroup_file(cg_path, "cpu.pressure"))
        }

    if 'memory' in modules:
        mem_stat = parse_kv(read_cgroup_file(cg_path, "memory.stat"))
        mem_events = parse_kv(read_cgroup_file(cg_path, "memory.events"))
        data['memory'] = {
            'cur': read_cgroup_file(cg_path, "memory.current"),
            'anon': _get(mem_stat, 'anon'),
            'file': _get(mem_stat, 'file'),
            'mjflt': _get(mem_stat, 'pgmajfault'),
            'oom': _get(mem_events, 'oom_kill'),
            'max': read_cgroup_file(cg_path, "memory.max"),
            'psi': parse_pressure(read_cgroup_file(cg_path, "memory.pressure"))
        }

    if 'io' in modules:
        io_stat = parse_io_stat(read_cgroup_file(cg_path, "io.stat"))
        data['io'] = {
            'rbytes': _get(io_stat, 'rbytes'),
            'wbytes': _get(io_stat, 'wbytes'),
            'rios': _get(io_stat, 'rios'),
            'wios': _get(io_stat, 'wios'),
            'weight': parse_io_weight(read_cgroup_file(cg_path, "io.weight")),
            'psi': parse_pressure(read_cgroup_file(cg_path, "io.pressure"))
        }

    if 'pids' in modules:
        data['pids'] = {
            'cur': read_cgroup_file(cg_path, "pids.current"),
            'max': read_cgroup_file(cg_path, "pids.max")
        }
    return data

def _delta(prev, curr, key):
    try:
        c = int(curr[key])
        p = int(prev[key])
    except (KeyError, TypeError, ValueError):
        return None
    # A counter that went backwards means the cgroup was recreated; count from zero
    return c - p if c >= p else c

def _pct(num, den):
    if num is None or den is None:
        return None
    return 100.0 * num / den if den > 0 else 0.0

def compute_row(prev, curr, elapsed):
    """Turn two snapshots taken `elapsed` seconds apart into per-column display values."""
    row = {}
    for mod, c in curr.items():
        p = prev.get(mod, {})
        vals = {}
        for name, _, _ in COL_DEF[mod]:
            vals[name] = _delta(p, c, name) if name in CUMULATIVE else c.get(name)
        if mod == 'cpu':
            wall_usec = elapsed * 1_000_000
            vals['%cpu'] = _pct(_delta(p, c, 'usage'), wall_usec)
            vals['%usr'] = _pct(_delta(p, c, 'usr'), wall_usec)
            vals['%sys'] = _pct(_delta(p, c, 'sys'), wall_usec)
            vals['thr%'] = _pct(vals['nr_thr'], _delta(p, c, 'nr_periods'))
        row[mod] = vals
    return row

def main():
    # Handle broken pipes cleanly (e.g. piping into head or grep)
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass

    # ANSI Colors (mimicking pidstat; auto-disabled when non-TTY or NO_COLOR is set)
    is_tty = sys.stdout.isatty()
    use_color = is_tty and "NO_COLOR" not in os.environ
    colors = ("\033[32m", "\033[34m") if use_color else ("", "")
    color_reset = "\033[0m" if use_color else ""

    parser = argparse.ArgumentParser(description="cgmon - pidstat-like tool for cgroups v2")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-p", "--pid", type=int, help="PID to resolve cgroup")
    group.add_argument("-c", "--cgroup", type=str, help="Cgroup path directly (e.g. user.slice)")
    parser.add_argument("-m", "--metrics", type=str, default="cpu,memory", help="Comma-separated controllers (cpu,memory,io,pids)")
    parser.add_argument("interval", type=float, nargs="?", default=1.0, help="Interval in seconds")
    parser.add_argument("count", type=int, nargs="?", default=-1, help="Count of updates")
    args = parser.parse_args()

    if args.interval <= 0:
        parser.error("interval must be greater than 0")
    if args.count != -1 and args.count < 1:
        parser.error("count must be a positive integer")
    if args.pid is not None and args.pid <= 0:
        parser.error("PID must be a positive integer")

    # dict.fromkeys drops duplicates while keeping the requested order
    modules = list(dict.fromkeys(m.strip() for m in args.metrics.split(",") if m.strip()))
    unknown = [m for m in modules if m not in COL_DEF]
    if unknown or not modules:
        parser.error(f"unknown metrics: {', '.join(unknown) or '(none given)'} "
                     f"(available: {', '.join(COL_DEF.keys())})")

    cg_path = CGROUP_ROOT
    if args.pid is not None:
        try:
            cg_path = resolve_pid_cgroup(args.pid)
        except OSError as e:
            print(f"Error resolving PID: {e}", file=sys.stderr)
            sys.exit(1)
        if cg_path is None:
            print("Error resolving PID: Could not find cgroups v2 (0::) entry", file=sys.stderr)
            sys.exit(1)
    elif args.cgroup:
        if os.path.isabs(args.cgroup):
            cg_path = os.path.abspath(args.cgroup)
        else:
            cg_path = os.path.normpath(os.path.join(CGROUP_ROOT, args.cgroup))

    if not os.path.isdir(cg_path):
        if not os.path.exists(os.path.join(CGROUP_ROOT, "cgroup.controllers")):
            print(f"Error: cgroups v2 does not appear to be mounted at {CGROUP_ROOT}.", file=sys.stderr)
        else:
            print(f"Error: Cgroup path {cg_path} does not exist.", file=sys.stderr)
        sys.exit(1)
    try:
        with open(os.path.join(cg_path, "cgroup.controllers")) as f:
            enabled = set(f.read().split())
    except OSError:
        print(f"Error: {cg_path} is not a cgroups v2 directory (no cgroup.controllers).", file=sys.stderr)
        sys.exit(1)

    for mod in modules:
        if mod not in enabled:
            print(f"Warning: the {mod} controller is not enabled for {cg_path}; "
                  f"its unavailable columns show '{MISSING}'.", file=sys.stderr)

    # Repeat the header once per screenful on a terminal; print it only once into a pipe
    header_every = max(shutil.get_terminal_size().lines - 2, 5) if is_tty else 0

    def print_header():
        h1 = [f"{'Time':<8}"]
        h2 = [f"{'':<8}"]
        for mod in modules:
            total_len = sum(w + 1 for _, w, _ in COL_DEF[mod]) - 1
            mod_title = f"-{mod}-"
            pad_len = max(0, total_len - len(mod_title))
            left_pad = pad_len // 2
            right_pad = pad_len - left_pad
            h1.append(("-" * left_pad) + mod_title + ("-" * right_pad))
            h2.append(' '.join(f"{name:>{w}}" for name, w, _ in COL_DEF[mod]))
        print(" ".join(h1))
        print(" ".join(h2))

    print_header()

    # Capture initial baseline snapshot so every reported row is an authentic interval delta
    prev_data = collect_data(cg_path, modules)
    prev_time = time.monotonic()
    next_tick = prev_time + args.interval
    sleep_time = next_tick - time.monotonic()
    if sleep_time > 0:
        time.sleep(sleep_time)

    count = 0
    rows_since_header = 0
    pid_cgroup = cg_path

    while args.count == -1 or count < args.count:
        if header_every and rows_since_header >= header_every:
            print_header()
            rows_since_header = 0

        if not os.path.exists(cg_path):
            print(f"\n[!] Target cgroup deleted ({cg_path}). Exiting.", file=sys.stderr)
            sys.exit(0)

        if args.pid is not None:
            # Tell the user once if the PID leaves the cgroup we are monitoring
            try:
                now_cgroup = resolve_pid_cgroup(args.pid) or "?"
            except OSError:
                now_cgroup = None
            if now_cgroup != pid_cgroup:
                if now_cgroup is None:
                    print(f"[!] PID {args.pid} exited; still monitoring {cg_path}", file=sys.stderr)
                elif now_cgroup != cg_path:
                    print(f"[!] PID {args.pid} moved to {now_cgroup}; still monitoring {cg_path}", file=sys.stderr)
                pid_cgroup = now_cgroup

        data = collect_data(cg_path, modules)
        now_time = time.monotonic()
        values = compute_row(prev_data, data, now_time - prev_time)
        prev_data, prev_time = data, now_time

        row = [datetime.datetime.now().strftime("%H:%M:%S")]
        for i, mod in enumerate(modules):
            cols = [f"{fmt_fn(values[mod][name]):>{w}}" for name, w, fmt_fn in COL_DEF[mod]]
            row.append(f"{colors[i % 2]}{' '.join(cols)}{color_reset}")

        try:
            print(" ".join(row))
            sys.stdout.flush()
        except BrokenPipeError:
            sys.exit(0)

        count += 1
        rows_since_header += 1
        if count == args.count:
            break

        next_tick += args.interval
        sleep_time = next_tick - time.monotonic()
        if sleep_time > 0:
            time.sleep(sleep_time)
        else:
            next_tick = time.monotonic()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    finally:
        close_file_cache()
