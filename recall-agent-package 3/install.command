#!/bin/sh
set -eu
recall_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$recall_source/install.sh" "$@"
