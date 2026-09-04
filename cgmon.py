#!/usr/bin/env python3
import argparse
import time
import sys
import os
import datetime

# ANSI Colors (mimicking pidstat)
COLOR_1 = "\033[32m" # Green
COLOR_2 = "\033[34m" # Blue
COLOR_RESET = "\033[0m"

# Formatting helpers
def format_usec(usec_str):
    if not usec_str or usec_str == "max": return str(usec_str)
    try:
        usec = int(usec_str)
        if usec < 1000:
            return f"{usec}us"
        elif usec < 1_000_000:
            return f"{usec//1000}ms"
        elif usec < 60_000_000:
            return f"{usec/1_000_000:.1f}s"
        else:
            return f"{usec/60_000_000:.1f}m"
    except ValueError:
        return usec_str

def format_bytes(bytes_str):
    if not bytes_str or bytes_str == "max": return str(bytes_str)
    try:
        b = int(bytes_str)
        if b < 1024:
            return f"{b}B"
        elif b < 1024**2:
            return f"{b/1024:.1f}K".replace('.0K', 'K')
        elif b < 1024**3:
            return f"{b/(1024**2):.1f}M".replace('.0M', 'M')
        else:
            return f"{b/(1024**3):.1f}G".replace('.0G', 'G')
    except ValueError:
        return bytes_str

def format_count(count_str):
    if not count_str or count_str == "max": return str(count_str)
    try:
        c = int(count_str)
        if c < 1000:
            return str(c)
        elif c < 1_000_000:
            return f"{c/1000:.1f}K".replace('.0K', 'K')
        else:
            return f"{c/1_000_000:.1f}M".replace('.0M', 'M')
    except ValueError:
        return count_str

def read_cgroup_file(cg_path, filename):
    filepath = os.path.join(cg_path, filename)
    if not os.path.exists(filepath):
        return ""
    try:
        with open(filepath, 'r') as f:
            return f.read().strip()
    except Exception:
        return ""

def parse_kv(content):
    res = {}
    if not content: return res
    for line in content.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            res[parts[0]] = parts[1]
    return res

def parse_pressure(content):
    if not content: return "0"
    res = {}
    for line in content.splitlines():
        if line.startswith("some"):
            parts = line.split()
            for p in parts:
                if p.startswith("total="):
                    res["total"] = p.split("=")[1]
    return res.get("total", "0")

def parse_io_stat(content):
    total = {'rbytes': 0, 'wbytes': 0, 'rios': 0, 'wios': 0}
    if not content: return total
    for line in content.splitlines():
        parts = line.split()
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=")
                if k in total:
                    total[k] += int(v)
    return {k: str(v) for k, v in total.items()}

def parse_io_weight(content):
    if not content: return "100" # default
    parts = content.split()
    if "default" in parts:
        try:
            idx = parts.index("default")
            return parts[idx+1]
        except:
            pass
    return parts[0] if parts else "100"

def main():
    parser = argparse.ArgumentParser(description="cgmon - vmstat-like tool for cgroups")
    parser.add_argument("-p", "--pid", type=int, help="PID to resolve cgroup")
    parser.add_argument("-c", "--cgroup", type=str, help="Cgroup path directly (e.g. user.slice)")
    parser.add_argument("-m", "--metrics", type=str, default="cpu,memory", help="Comma-separated controllers (cpu,memory,io,pids)")
    parser.add_argument("interval", type=int, nargs="?", default=1, help="Interval in seconds")
    parser.add_argument("count", type=int, nargs="?", default=-1, help="Count of updates")
    args = parser.parse_args()

    cg_path = "/sys/fs/cgroup"
    if args.pid:
        try:
            with open(f"/proc/{args.pid}/cgroup", "r") as f:
                cgroup_line = f.read().strip()
                cg_suffix = cgroup_line.split(":")[-1].lstrip("/")
                cg_path = os.path.join(cg_path, cg_suffix)
        except Exception as e:
            print(f"Error resolving PID: {e}")
            sys.exit(1)
    elif args.cgroup:
        cg_path = os.path.join(cg_path, args.cgroup.lstrip("/"))

    if not os.path.exists(cg_path):
        print(f"Error: Cgroup path {cg_path} does not exist.")
        sys.exit(1)

    modules = args.metrics.split(",")
    
    COL_DEF = {
        'cpu': [
            ('usage', 7, format_usec), ('usr', 7, format_usec), ('sys', 7, format_usec),
            ('nr_thr', 6, format_count), ('thr_us', 7, format_usec),
            ('max', 7, format_usec), ('psi', 7, format_usec)
        ],
        'memory': [
            ('cur', 7, format_bytes), ('anon', 7, format_bytes), ('file', 7, format_bytes),
            ('mjflt', 6, format_count), ('oom', 5, format_count),
            ('max', 7, format_bytes), ('psi', 7, format_usec)
        ],
        'io': [
            ('rbytes', 8, format_bytes), ('wbytes', 8, format_bytes),
            ('rios', 7, format_count), ('wios', 7, format_count),
            ('rbps', 7, format_bytes), ('wbps', 7, format_bytes),
            ('riops', 7, format_count), ('wiops', 7, format_count),
            ('weight', 6, format_count), ('psi', 7, format_usec)
        ],
        'pids': [
            ('cur', 6, format_count), ('max', 6, format_count)
        ]
    }

    def print_header():
        h1 = [f"{'Time':<11}"]
        h2 = [f"{'':<11}"]
        for mod in modules:
            if mod not in COL_DEF: continue
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
    
    count = 0
    rows_per_minute = max(1, int(60 / args.interval)) if args.interval > 0 else 60
    
    while args.count == -1 or count < args.count:
        if count > 0 and count % rows_per_minute == 0:
            print_header()
            
        now = datetime.datetime.now().strftime("%I:%M:%S %p")
        row = [f"{now:<11}"]
        
        cpu_stat = parse_kv(read_cgroup_file(cg_path, "cpu.stat"))
        mem_stat = parse_kv(read_cgroup_file(cg_path, "memory.stat"))
        mem_events = parse_kv(read_cgroup_file(cg_path, "memory.events"))
        io_stat = parse_io_stat(read_cgroup_file(cg_path, "io.stat"))
        
        cpu_max = read_cgroup_file(cg_path, "cpu.max")
        if cpu_max: cpu_max = cpu_max.split()[0]
        
        mem_max = read_cgroup_file(cg_path, "memory.max")
        io_weight = parse_io_weight(read_cgroup_file(cg_path, "io.weight"))
        
        DATA = {
            'cpu': {
                'usage': cpu_stat.get('usage_usec', '0'),
                'usr': cpu_stat.get('user_usec', '0'),
                'sys': cpu_stat.get('system_usec', '0'),
                'nr_thr': cpu_stat.get('nr_throttled', '0'),
                'thr_us': cpu_stat.get('throttled_usec', '0'),
                'max': cpu_max or 'max',
                'psi': parse_pressure(read_cgroup_file(cg_path, "cpu.pressure"))
            },
            'memory': {
                'cur': read_cgroup_file(cg_path, "memory.current") or '0',
                'anon': mem_stat.get('anon', '0'),
                'file': mem_stat.get('file', '0'),
                'mjflt': mem_stat.get('pgmajfault', '0'),
                'oom': mem_events.get('oom_kill', '0'),
                'max': mem_max or 'max',
                'psi': parse_pressure(read_cgroup_file(cg_path, "memory.pressure"))
            },
            'io': {
                'rbytes': io_stat.get('rbytes', '0'),
                'wbytes': io_stat.get('wbytes', '0'),
                'rios': io_stat.get('rios', '0'),
                'wios': io_stat.get('wios', '0'),
                'rbps': 'max',
                'wbps': 'max',
                'riops': 'max',
                'wiops': 'max',
                'weight': io_weight,
                'psi': parse_pressure(read_cgroup_file(cg_path, "io.pressure"))
            },
            'pids': {
                'cur': read_cgroup_file(cg_path, "pids.current") or '0',
                'max': read_cgroup_file(cg_path, "pids.max") or 'max'
            }
        }
        
        color_idx = 0
        for mod in modules:
            if mod not in COL_DEF: continue
            
            color = COLOR_1 if (color_idx % 2 == 0) else COLOR_2
            color_idx += 1
            
            cols = []
            for name, w, fmt_fn in COL_DEF[mod]:
                raw_val = DATA[mod].get(name, '0')
                formatted = fmt_fn(raw_val)
                cols.append(f"{formatted:>{w}}")
            row.append(f"{color}{' '.join(cols)}{COLOR_RESET}")
            
        print(" ".join(row))
        sys.stdout.flush()
        
        count += 1
        if count == args.count:
            break
        time.sleep(args.interval)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
