#!/usr/bin/env python3
import multiprocessing
import os
import signal
import time
import tempfile

def ignore_sigint():
    # Ctrl-C reaches the whole process group; let the main process shut workers down
    signal.signal(signal.SIGINT, signal.SIG_IGN)

def cpu_burner():
    ignore_sigint()
    print(f"[*] Started CPU Burner (Worker PID: {os.getpid()})")
    while True:
        # Tight CPU loop
        _ = 3.14159 ** 2.71828

def memory_hog():
    ignore_sigint()
    print(f"[*] Started Memory Hog (Worker PID: {os.getpid()})")
    memory_chunks = []
    # Cap at ~2GB (100 * 20MB) safety ceiling to prevent host crashes if run naked
    while len(memory_chunks) < 100:
        memory_chunks.append(b'A' * 20 * 1024 * 1024)
        time.sleep(0.5)
    print("[*] Memory Hog reached 2GB safety ceiling. Sleeping to hold memory...")
    while True:
        time.sleep(1)

def io_spammer():
    ignore_sigint()
    print(f"[*] Started I/O Spammer (Worker PID: {os.getpid()})")
    # Stream a 50MB file in 1MB chunks so this worker stays small and the
    # OOM killer targets the Memory Hog, not us
    chunk = b'Z' * 1024 * 1024
    file_mb = 50
    with tempfile.TemporaryFile() as f:
        while True:
            try:
                # Write and force sync to disk to generate actual block I/O
                f.seek(0)
                for _ in range(file_mb):
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
                # Invalidate page cache so subsequent read generates real disk block I/O
                try:
                    os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                except (AttributeError, OSError):
                    pass
                # Read it back to generate read I/O
                f.seek(0)
                while f.read(len(chunk)):
                    pass
                time.sleep(0.1)
            except OSError as e:
                print(f"[!] I/O Spammer error: {e}")
                time.sleep(1)

def spawn(name):
    if "CPU" in name:
        target = cpu_burner
    elif "Memory" in name:
        target = memory_hog
    else:
        target = io_spammer
    p = multiprocessing.Process(target=target)
    p.start()
    return p

if __name__ == "__main__":
    main_pid = os.getpid()
    print("==========================================================")
    print("🚀 Production Simulator Started!")
    print(f"Main Process PID: {main_pid}")
    print("==========================================================\n")
    if os.path.exists("/.dockerenv"):
        # Our PID is container-local; cgmon must run on the host
        print("Running inside a container. To monitor it with cgmon, run on the host:")
        print("    cd ~/cgmon && ./demo.sh observe 1\n")
    else:
        print("To monitor this with cgmon, open a second SSH terminal and run:")
        print(f"    cd ~/cgmon && ./cgmon -p {main_pid} -m cpu,memory,io,pids 1\n")

    # Track workers explicitly by name for respawning
    names = ["CPU Burner 1", "CPU Burner 2", "Memory Hog", "I/O Spammer"]
    workers = {}

    try:
        print("[*] Sleeping for 15 seconds so you can observe the baseline environment in cgmon...")
        time.sleep(15)

        for name in names:
            workers[name] = spawn(name)
            print(f"[*] Started {name}. Waiting 5 seconds before launching the next...")
            time.sleep(5)

        # Keep main thread alive and monitor workers
        while True:
            for name, p in list(workers.items()):
                if not p.is_alive():
                    exit_code = p.exitcode
                    # SIGKILL under a memory limit is almost always the OOM killer,
                    # but cgmon's 'oom' column is the authoritative signal
                    cause = "Killed by SIGKILL (likely OOM)" if exit_code == -signal.SIGKILL else f"Exited with code {exit_code}"
                    print(f"\n[!] Worker '{name}' died (PID: {p.pid}). Reason: {cause}")
                    print(f"[*] Respawning '{name}' in 3 seconds to generate more events...")
                    time.sleep(3)
                    workers[name] = spawn(name)
                    print(f"[*] Respawned '{name}' (New PID: {workers[name].pid})")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Stopping Simulator...")
        for p in workers.values():
            p.terminate()
        for p in workers.values():
            p.join(timeout=1)
