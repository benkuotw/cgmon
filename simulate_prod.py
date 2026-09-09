#!/usr/bin/env python3
import multiprocessing
import os
import time
import tempfile

def cpu_burner():
    print(f"[*] Started CPU Burner (Worker PID: {os.getpid()})")
    while True:
        # Tight CPU loop
        _ = 3.14159 ** 2.71828

def memory_hog():
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
    print(f"[*] Started I/O Spammer (Worker PID: {os.getpid()})")
    data = b'Z' * 50 * 1024 * 1024  # 50MB chunk
    with tempfile.NamedTemporaryFile() as f:
        while True:
            try:
                # Write and force sync to disk to generate actual block I/O
                f.seek(0)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
                # Read it back to generate read I/O
                f.seek(0)
                _ = f.read()
                time.sleep(0.1)
            except Exception:
                pass

if __name__ == "__main__":
    main_pid = os.getpid()
    print("==========================================================")
    print("🚀 Production Simulator Started!")
    print(f"Main Process PID: {main_pid}")
    print("==========================================================\n")
    print("To monitor this with cgmon, open a second SSH terminal and run:")
    print(f"    cd ~/cgmon && ./cgmon -p {main_pid} -m cpu,memory,io,pids 1\n")
    
    print("[*] Sleeping for 15 seconds so you can observe the baseline environment in cgmon...")
    time.sleep(15)
    
    # Track workers explicitly by name for respawning
    workers = {
        "CPU Burner 1": multiprocessing.Process(target=cpu_burner),
        "CPU Burner 2": multiprocessing.Process(target=cpu_burner),
        "Memory Hog": multiprocessing.Process(target=memory_hog),
        "I/O Spammer": multiprocessing.Process(target=io_spammer)
    }
    
    for name, p in workers.items():
        p.start()
        print(f"[*] Started {name}. Waiting 5 seconds before launching the next...")
        time.sleep(5)
        
    try:
        # Keep main thread alive and monitor workers
        while True:
            for name, p in list(workers.items()):
                if not p.is_alive():
                    print(f"\n[!] Worker '{name}' died (PID: {p.pid}). Likely OOM Killed!")
                    print(f"[*] Respawning '{name}' in 3 seconds to generate more events...")
                    time.sleep(3)
                    
                    if "CPU" in name:
                        new_p = multiprocessing.Process(target=cpu_burner)
                    elif "Memory" in name:
                        new_p = multiprocessing.Process(target=memory_hog)
                    else:
                        new_p = multiprocessing.Process(target=io_spammer)
                        
                    new_p.start()
                    workers[name] = new_p
                    print(f"[*] Respawned '{name}' (New PID: {new_p.pid})")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Stopping Simulator...")
        for name, p in workers.items():
            p.terminate()
