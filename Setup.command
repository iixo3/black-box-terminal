#!/bin/zsh
set -eu
umask 077
cd -- "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python setup_local.py
read "?Press Enter to close."
