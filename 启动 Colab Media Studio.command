#!/bin/zsh
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")"

if ! command -v colab >/dev/null 2>&1; then
  echo "检测到尚未安装 google-colab-cli，正在自动安装..."
  if command -v uv >/dev/null 2>&1; then
    uv tool install google-colab-cli
  else
    pip3 install --user google-colab-cli
  fi
fi

exec python3 local_app/server.py
