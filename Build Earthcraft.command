#!/bin/zsh
set -e
ROOT="${0:A:h}"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Earthcraft's virtual environment is missing. See README.md for setup."
  exit 1
fi
if [[ -f location.kmz && -f location.kml ]]; then
  echo 'Keep one location export: location.kml or location.kmz, not both.'
  exit 1
elif [[ -f location.kmz ]]; then
  exec "$PYTHON" scripts/earth_location.py --kml location.kmz
elif [[ -f location.kml ]]; then
  exec "$PYTHON" scripts/earth_location.py --kml location.kml
fi
exec "$PYTHON" scripts/earthcraft.py --replay-check
