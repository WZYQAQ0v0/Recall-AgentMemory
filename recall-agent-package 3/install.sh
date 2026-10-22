#!/bin/sh
set -eu

recall_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
recall_probe='import sys,sqlite3; assert sys.version_info >= (3,9); sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE probe USING fts5(text)")'

find_recall_python() {
    for recall_candidate in "${RECALL_PYTHON:-}" python3 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
        [ -n "$recall_candidate" ] || continue
        recall_executable=$(command -v "$recall_candidate" 2>/dev/null || true)
        [ -n "$recall_executable" ] || continue
        if "$recall_executable" -B -c "$recall_probe" >/dev/null 2>&1; then
            printf '%s\n' "$recall_executable"
            return 0
        fi
    done
    return 1
}

recall_python=$(find_recall_python || true)
recall_preview=0
for recall_arg in "$@"; do
    [ "$recall_arg" != "--plan" ] || recall_preview=1
done
if [ -z "$recall_python" ] && [ "$recall_preview" -eq 0 ] && [ "${RECALL_SKIP_RUNTIME_INSTALL:-0}" != "1" ]; then
    recall_brew=$(command -v brew 2>/dev/null || true)
    if [ -z "$recall_brew" ]; then
        for recall_candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
            if [ -x "$recall_candidate" ]; then recall_brew=$recall_candidate; break; fi
        done
    fi
    if [ -n "$recall_brew" ]; then
        "$recall_brew" install python
    elif [ "$(uname -s)" = "Linux" ] && [ "$(id -u)" -eq 0 ]; then
        if command -v apt-get >/dev/null 2>&1; then
            apt-get update
            apt-get install -y python3
        elif command -v dnf >/dev/null 2>&1; then
            dnf install -y python3
        fi
    fi
    recall_python=$(find_recall_python || true)
fi
if [ -z "$recall_python" ]; then
    printf '%s\n' "Recall needs Python 3.9+ with SQLite FTS5." "Install Python from python.org or your OS package manager, then rerun this script." "An Agent can set RECALL_PYTHON to an existing compatible interpreter. No administrator changes were attempted." >&2
    exit 3
fi
exec "$recall_python" -B "$recall_source/recallctl.py" install "$@"
