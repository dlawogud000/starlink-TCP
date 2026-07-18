#!/usr/bin/env bash
set -euo pipefail

SRC_ROOT="$1"
DST_DIR="$2"
TARGET_FILE="$3"
FILTER="${4:-}"

if [ $# -lt 3 ]; then
    echo "Usage: $0 <src_root> <dst_dir> <target_file> [folder_name_filter]"
    echo "Example: $0 ./logs ./collected iperf3_aggregate.csv tcp_bbr_downlink_2flow_rc"
    exit 1
fi

mkdir -p "$DST_DIR"

base="${TARGET_FILE%.*}"
ext="${TARGET_FILE##*.}"

if [ "$base" = "$ext" ]; then
    ext=""
else
    ext=".$ext"
fi

i=1

find "$SRC_ROOT" -mindepth 1 -maxdepth 1 -type d | sort | while read -r dir; do
    name="$(basename "$dir")"

    if [ -n "$FILTER" ] && [[ "$name" != *"$FILTER"* ]]; then
        continue
    fi

    src="$dir/$TARGET_FILE"

    if [ ! -f "$src" ]; then
        continue
    fi

    dst="$DST_DIR/${base}${i}${ext}"

    cp "$src" "$dst"

    echo "Copied: $src -> $dst"

    i=$((i + 1))
done