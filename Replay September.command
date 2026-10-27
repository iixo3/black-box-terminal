#!/bin/zsh
set -eu
umask 077
cd -- "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print "Run Setup.command first."
  read "?Press Enter to close."
  exit 1
fi
default_year=$(.venv/bin/python -c 'from datetime import datetime; from zoneinfo import ZoneInfo; d=datetime.now(ZoneInfo("UTC")); print(d.year if d.month >= 9 else d.year-1)')
read "replay_year?September year [${default_year}]: "
replay_year=${replay_year:-$default_year}
if [[ ! "$replay_year" =~ '^[0-9]{4}$' ]]; then
  print "Enter a four-digit year."
  exit 1
fi
print "Historical review only. No news will be sent and live checkpoints will remain unchanged."
.venv/bin/python watcher.py replay --start "${replay_year}-09-01" --end "${replay_year}-10-01"
read "?Report paths are shown above. Press Enter to close."
