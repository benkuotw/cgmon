#!/usr/bin/env python3
import argparse
import datetime
import os
import signal
import sys
import time

# Handle broken pipes cleanly (e.g. piping into head or grep)
try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass

# ANSI Colors (mimicking pidstat; auto-disabled when non-TTY or NO_COLOR is set)
USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ
COLOR_1 = "\033[32m" if USE_COLOR else ""
COLOR_2 = "\033[34m" if USE_COLOR else ""
COLOR_RESET = "\033[0m" if USE_COLOR else ""

# Formatting helpers
def format_usec(usec_str):
    if not usec_str or usec_str == "max":
        return str(usec_str)
    try:
        usec = int(usec_str)
        if usec < 1000:
            return f"{usec}us"
        elif usec < 1_000_000:
            ms = usec / 1000
            return f"{ms:.1f}ms".replace(".0ms", "ms") if ms < 10 else f"{int(ms)}ms"
        elif usec < 60_000_000:
            return f"{usec/1_000_000:.1f}s"
        else:
            return f"{usec/60_000_000:.1f}m"
    except ValueError:
        return str(usec_str)

def format_bytes(bytes_str):
    if not bytes_str or bytes_str == "max":
        return str(bytes_str)
    try:
        b = int(bytes_str)
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
    except ValueError:
        return str(bytes_str)

def format_count(count_str):
    if not count_str or count_str == "max":
        return str(count_str)
    try:
        c = int(count_str)
        if c < 1000:
            return str(c)
        elif c < 1_000_000:
            return f"{c/1000:.1f}K".replace('.0K', 'K')
        else:
            return f"{c/1_000_000:.1f}M".replace('.0M', 'M')
    except ValueError:
        return str(count_str)

def format_cpu_max(val_str):
    return str(val_str)

def read_cgroup_file(cg_path, filename):
    filepath = os.path.join(cg_path, filename)
    try:
        with open(filepath, 'r') as f:
            return f.read().strip()
    except (FileNotFoundError, IOError, OSError):
        return ""

def parse_kv(content):
    res = {}
    if not content:
        return res
    for line in content.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            res[parts[0]] = parts[1]
    return res

def parse_cpu_max(content):
    if not content:
        return "max"
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
    except (ValueError, ZeroDivisionError):
        return parts[0]

def parse_pressure(content):
    if not content:
        return "0"
    for line in content.splitlines():
        if line.startswith("some"):
            parts = line.split()
            for p in parts:
                if p.startswith("total="):
                    return p.split("=")[1]
    return "0"

def parse_io_stat(content):
    total = {'rbytes': 0, 'wbytes': 0, 'rios': 0, 'wios': 0}
    if content:
        for line in content.splitlines():
            parts = line.split()
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=")
                    if k in total:
                        try:
                            total[k] += int(v)
                        except ValueError:
                            pass
    return {k: str(v) for k, v in total.items()}

def parse_io_weight(content):
    if not content:
        return "100"
    for line in content.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "default" and len(parts) >= 2:
            return parts[1]
        if len(parts) >= 2:
            return parts[1]
        return parts[0]
    return "100"

COL_DEF = {
    'cpu': [
        ('usage', 7, format_usec), ('usr', 7, format_usec), ('sys', 7, format_usec),
        ('nr_thr', 6, format_count), ('thr_us', 7, format_usec),
        ('max', 7, format_cpu_max), ('psi', 7, format_usec)
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

def collect_data(cg_path, modules):
    data = {}
    if 'cpu' in modules:
        cpu_stat = parse_kv(read_cgroup_file(cg_path, "cpu.stat"))
        cpu_max = read_cgroup_file(cg_path, "cpu.max")
        data['cpu'] = {
            'usage': cpu_stat.get('usage_usec', '0'),
            'usr': cpu_stat.get('user_usec', '0'),
            'sys': cpu_stat.get('system_usec', '0'),
            'nr_thr': cpu_stat.get('nr_throttled', '0'),
            'thr_us': cpu_stat.get('throttled_usec', '0'),
            'max': parse_cpu_max(cpu_max),
            'psi': parse_pressure(read_cgroup_file(cg_path, "cpu.pressure"))
        }

    if 'memory' in modules:
        mem_stat = parse_kv(read_cgroup_file(cg_path, "memory.stat"))
        mem_events = parse_kv(read_cgroup_file(cg_path, "memory.events"))
        mem_max = read_cgroup_file(cg_path, "memory.max")
        data['memory'] = {
            'cur': read_cgroup_file(cg_path, "memory.current") or '0',
            'anon': mem_stat.get('anon', '0'),
            'file': mem_stat.get('file', '0'),
            'mjflt': mem_stat.get('pgmajfault', '0'),
            'oom': mem_events.get('oom_kill', '0'),
            'max': mem_max or 'max',
            'psi': parse_pressure(read_cgroup_file(cg_path, "memory.pressure"))
        }

    if 'io' in modules:
        io_stat = parse_io_stat(read_cgroup_file(cg_path, "io.stat"))
        data['io'] = {
            'rbytes': io_stat.get('rbytes', '0'),
            'wbytes': io_stat.get('wbytes', '0'),
            'rios': io_stat.get('rios', '0'),
            'wios': io_stat.get('wios', '0'),
            'weight': parse_io_weight(read_cgroup_file(cg_path, "io.weight")),
            'psi': parse_pressure(read_cgroup_file(cg_path, "io.pressure"))
        }

    if 'pids' in modules:
        data['pids'] = {
            'cur': read_cgroup_file(cg_path, "pids.current") or '0',
            'max': read_cgroup_file(cg_path, "pids.max") or 'max'
        }
    return data

def main():
    parser = argparse.ArgumentParser(description="cgmon - pidstat-like tool for cgroups")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-p", "--pid", type=int, help="PID to resolve cgroup")
    group.add_argument("-c", "--cgroup", type=str, help="Cgroup path directly (e.g. user.slice)")
    parser.add_argument("-m", "--metrics", type=str, default="cpu,memory", help="Comma-separated controllers (cpu,memory,io,pids)")
    parser.add_argument("interval", type=float, nargs="?", default=1.0, help="Interval in seconds")
    parser.add_argument("count", type=int, nargs="?", default=-1, help="Count of updates")
    args = parser.parse_args()

    if args.interval <= 0:
        print("Error: interval must be greater than 0")
        sys.exit(1)

    cg_path = "/sys/fs/cgroup"
    if args.pid:
        try:
            with open(f"/proc/{args.pid}/cgroup", "r") as f:
                found_v2 = False
                cg_suffix = ""
                for line in f:
                    if line.startswith("0::"):
                        found_v2 = True
                        cg_suffix = line.strip().split(":")[-1].lstrip("/")
                        break
                if not found_v2:
                    raise Exception("Could not find cgroups v2 (0::) entry")
                cg_path = os.path.join(cg_path, cg_suffix)
        except Exception as e:
            print(f"Error resolving PID: {e}")
            sys.exit(1)
    elif args.cgroup:
        if os.path.isabs(args.cgroup):
            cg_path = os.path.abspath(args.cgroup)
        else:
            cg_path = os.path.join(cg_path, args.cgroup)

    if not os.path.exists(cg_path):
        print(f"Error: Cgroup path {cg_path} does not exist.")
        sys.exit(1)

    modules = [m.strip() for m in args.metrics.split(",") if m.strip()]
    valid_modules = [m for m in modules if m in COL_DEF]
    if not valid_modules:
        print(f"Error: No valid metrics specified. Available: {', '.join(COL_DEF.keys())}")
        sys.exit(1)
    modules = valid_modules

    def print_header():
        h1 = [f"{'Time':<11}"]
        h2 = [f"{'':<11}"]
        for mod in modules:
            total_len = sum(w + 1 for _, w, _ in COL_DEF[mod]) - 1
            mod_title = f"-{mod}-"
            pad_len = max(0, total_len - len(mod_title))
            left_pad = pad_len // 2
            right_pad = pad_len - left_pad
            sep_str = ("-" * left_pad) + mod_title + ("-" * right_pad)

            h1.append(sep_str)

            cols = []
            for name, w, _ in COL_DEF[mod]:
                cols.append(f"{name:>{w}}")
            h2.append(' '.join(cols))

        print(" ".join(h1))
        print(" ".join(h2))

    print_header()

    # Capture initial baseline snapshot so every reported row is an authentic interval delta
    PREV_DATA = collect_data(cg_path, modules)
    next_tick = time.time() + args.interval
    sleep_time = next_tick - time.time()
    if sleep_time > 0:
        time.sleep(sleep_time)

    count = 0
    CUMULATIVE = {'usage', 'usr', 'sys', 'nr_thr', 'thr_us', 'psi', 'mjflt', 'oom', 'rbytes', 'wbytes', 'rios', 'wios'}

    while args.count == -1 or count < args.count:
        if count > 0 and count % 10 == 0:
            print_header()

        if not os.path.exists(cg_path):
            print(f"\n[!] Target cgroup deleted ({cg_path}). Exiting.")
            sys.exit(0)

        DATA = collect_data(cg_path, modules)
        now = datetime.datetime.now().strftime("%I:%M:%S %p")
        row = [f"{now:<11}"]

        color_idx = 0
        for mod in modules:
            color = COLOR_1 if (color_idx % 2 == 0) else COLOR_2
            color_idx += 1

            cols = []
            for name, w, fmt_fn in COL_DEF[mod]:
                raw_str = DATA[mod].get(name, '0')

                if name in CUMULATIVE:
                    try:
                        curr_val = int(raw_str)
                        prev_val = int(PREV_DATA.get(mod, {}).get(name, '0'))
                        val = max(0, curr_val - prev_val)
                        formatted = fmt_fn(str(val))
                    except ValueError:
                        formatted = fmt_fn(raw_str)
                else:
                    formatted = fmt_fn(raw_str)

                cols.append(f"{formatted:>{w}}")
            row.append(f"{color}{' '.join(cols)}{COLOR_RESET}")

        PREV_DATA = DATA

        try:
            print(" ".join(row))
            sys.stdout.flush()
        except BrokenPipeError:
            sys.exit(0)

        count += 1
        if count == args.count:
            break

        next_tick += args.interval
        sleep_time = next_tick - time.time()
        if sleep_time > 0:
            time.sleep(sleep_time)
        else:
            next_tick = time.time()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
