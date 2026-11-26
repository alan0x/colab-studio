#!/bin/zsh
cd "$(dirname "$0")"
exec python3 local_app/server.py
