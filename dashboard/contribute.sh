#!/bin/sh
# Earthcraft: build map tiles on this computer and offer them to the dataset.
#
#   curl -fsSL https://ericspencer.us/earthcraft/contribute.sh | sh
#   curl -fsSL https://ericspencer.us/earthcraft/contribute.sh | sh -s -- --tiles 20
#
# It keeps everything in one folder (~/.earthcraft, or $EARTHCRAFT_DIR): the code, a private
# Python environment and at most about 30 GB of working files. Delete the folder to remove it.
# Arguments go to scripts/contribute.py (--tiles N, --cores N, --shard i/N, --dry-run).
set -eu

DIR="${EARTHCRAFT_DIR:-$HOME/.earthcraft}"
REPO="${EARTHCRAFT_REPO:-https://github.com/EricSpencer00/earthcraft}"
BRANCH="${EARTHCRAFT_BRANCH:-main}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf 'earthcraft: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git is missing. Install it (macOS: xcode-select --install) and run this again."

if [ -d "$DIR/.git" ]; then
  say "Updating $DIR"
  git -C "$DIR" pull --quiet --ff-only origin "$BRANCH" || say "Could not update; using the code already there."
else
  say "Downloading Earthcraft to $DIR"
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"

# About 30 GB of working files at most; the tool itself refuses to go below 10 GB free.
free_gb=$(df -Pk "$DIR" | awk 'NR==2 {printf "%d", $4/1048576}')
[ "$free_gb" -ge 20 ] || die "only $free_gb GB free on this disk; 20 GB or more is needed."

python_ok() { "$1" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1; }

if [ ! -x .venv/bin/python ] || ! python_ok .venv/bin/python; then
  say "Setting up Python (a few minutes, once)"
  rm -rf .venv
  if command -v uv >/dev/null 2>&1; then
    uv venv --quiet --python 3.11 .venv
  elif command -v python3 >/dev/null 2>&1 && python_ok python3; then
    python3 -m venv .venv
  else
    say "Python 3.11 is not installed; fetching uv (astral.sh) to provide it"
    curl -fsSL https://astral.sh/uv/install.sh | sh >/dev/null
    PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; export PATH
    command -v uv >/dev/null 2>&1 || die "could not install uv. Install Python 3.11 and run this again."
    uv venv --quiet --python 3.11 .venv
  fi
fi

stamp=.venv/.earthcraft-requirements
want=$(cat requirements-*.txt | cksum)
if [ "$(cat "$stamp" 2>/dev/null || true)" != "$want" ]; then
  say "Installing packages"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --quiet --python .venv/bin/python -r requirements-contribute.txt
  else
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -r requirements-contribute.txt
  fi
  printf '%s' "$want" > "$stamp"
fi

case " $* " in
  *" --dry-run "*|*" --check-tile"*) ;;  # nothing is uploaded: no account needed
  *)
    if [ -z "${HF_TOKEN:-}" ] && ! .venv/bin/hf auth whoami >/dev/null 2>&1; then
      say "Sign in to Hugging Face (free): create a token with write access at"
      say "  https://huggingface.co/settings/tokens"
      [ -r /dev/tty ] || die "no terminal to sign in from. Set HF_TOKEN and run this again."
      .venv/bin/hf auth login </dev/tty
    fi ;;
esac

say "Building tiles. Press Ctrl-C to stop; finished tiles are already uploaded."
exec .venv/bin/python scripts/contribute.py "$@"
