#!/bin/bash

TARGET_IP="$1"

if [ -z "$TARGET_IP" ]; then
    echo "Usage: $0 <target_ip>"
    exit 1
fi

pop_ip=$(traceroute -n -i "enx588694fda289" "$TARGET_IP" | awk '$2 ~ /^206\.224/ {print $2; exit}')

if [ -z "$pop_ip" ]; then
    echo "ERROR: No POP IP found" >&2
    exit 1
fi

# ping check
if ! ping -c 3 -W 2 "$pop_ip" > /dev/null 2>&1; then
    last_octet=$(echo "$pop_ip" | awk -F. '{print $4}')
    xor_octet=$((last_octet ^ 1))
    new_ip="$(echo "$pop_ip" | awk -F. '{print $1"."$2"."$3"."}')$xor_octet"

    if ping -c 3 -W 2 "$new_ip" > /dev/null 2>&1; then
        pop_ip="$new_ip"
    else
        echo "ERROR: POP IP unreachable" >&2
        exit 1
    fi
fi

echo "$pop_ip"
