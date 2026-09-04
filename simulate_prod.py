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
    try:
        while True:
            # Allocate 20MB chunks repeatedly
            memory_chunks.append(b'A' * 20 * 1024 * 1024)
            time.sleep(0.5)
    except MemoryError:
        print("[!] Memory Hog hit a MemoryError (OOM limit reached!)")

def io_spammer():
    print(f"[*] Started I/O Spammer (Worker PID: {os.getpid()})")
    temp_file = os.path.join(tempfile.gettempdir(), 'cgmon_io_test.dat')
    data = b'Z' * 50 * 1024 * 1024  # 50MB chunk
    while True:
        try:
            # Write and force sync to disk to generate actual block I/O
            with open(temp_file, 'wb') as f:
                f.write(data)
                os.fsync(f.fileno())
            # Read it back to generate read I/O
            with open(temp_file, 'rb') as f:
                _ = f.read()
            time.sleep(0.1)
        except Exception as e:
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
    
    # Spawn the workers
    processes = [
        multiprocessing.Process(target=cpu_burner),
        multiprocessing.Process(target=cpu_burner), # 2 CPU threads
        multiprocessing.Process(target=memory_hog),
        multiprocessing.Process(target=io_spammer)
    ]
    
    for i, p in enumerate(processes):
        p.start()
        if i < len(processes) - 1:
            print("[*] Started a new worker. Waiting 10 seconds before launching the next...")
            time.sleep(10)
        
    try:
        # Keep main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Stopping Simulator...")
        for p in processes:
            p.terminate()
        # Clean up temp file
        temp_file = os.path.join(tempfile.gettempdir(), 'cgmon_io_test.dat')
        if os.path.exists(temp_file):
            os.remove(temp_file)
