#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cp "$HERE/reference.py" /app/route_model.py
chmod 0644 /app/route_model.py
