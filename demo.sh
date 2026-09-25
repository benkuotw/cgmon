#!/usr/bin/env bash
# Run simulate_prod.py in a resource-limited, non-root container and watch it with cgmon.
set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
IMAGE="${IMAGE:-cgmon-sim}"
NAME="${NAME:-cgmon-sim}"

usage() {
    echo "Usage: $0 {build|up|logs|observe [interval] [count]|down}" >&2
    exit 1
}

case "${1:-}" in
    build)
        docker build -t "$IMAGE" "$DIR"
        ;;
    up)
        if docker container inspect "$NAME" >/dev/null 2>&1; then
            echo "Error: container '$NAME' already exists (try: $0 down)" >&2
            exit 1
        fi
        # --memory-swap == --memory disables swap so the OOM killer actually fires
        docker run -d --init --name "$NAME" \
            --cpus=0.5 --memory=150m --memory-swap=150m --pids-limit=64 \
            --cap-drop=ALL --security-opt=no-new-privileges \
            "$IMAGE"
        ;;
    logs)
        docker logs -f "$NAME"
        ;;
    observe)
        pid="$(docker inspect -f '{{if .State.Running}}{{.State.Pid}}{{end}}' "$NAME" 2>/dev/null || true)"
        if [ -z "$pid" ]; then
            echo "Error: container '$NAME' is not running (try: $0 up)" >&2
            exit 1
        fi
        shift
        exec "$DIR/cgmon" -p "$pid" -m cpu,memory,io,pids "${1:-1}" ${2:+"$2"}
        ;;
    down)
        docker rm -f "$NAME"
        ;;
    *)
        usage
        ;;
esac
