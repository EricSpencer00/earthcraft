#!/usr/bin/env bash
# Earthcraft always-on pipeline host (AWS EC2 or Oracle Cloud Always Free, Ubuntu 24.04).
# See docs/AWS_PIPELINE.md and docs/ORACLE_PIPELINE.md.
#
# On the coordinator (MacBook, has the AWS CLI, the ssh alias and the private files):
#   scripts/aws_pipeline_setup.sh launch [--count N] [--dry-run]
#                                                   N more identical spot boxes (default 1); every box is
#                                                   re-tagged EarthcraftShard=i/T and picks up its share itself
#   scripts/aws_pipeline_setup.sh push [HOST]       copy private inputs (client JAR, templates, frame), start;
#                                                   on an Oracle host also the HF token (no SSM or S3 there)
# On the instance (cloud-init runs `bootstrap` as root; the units call the rest):
#   aws_pipeline_setup.sh bootstrap                 packages, checkout, venv, units, control seed
#   aws_pipeline_setup.sh run                       service entry: token from SSM, exec the supervisor
#   aws_pipeline_setup.sh update                    timer: fast-forward, reinstall, graceful restart
#   aws_pipeline_setup.sh validate                  timer: check open contribution PRs, comment (never merges)
#   aws_pipeline_setup.sh status [--json FILE]      tiles done, tiles/hour, last commit, disk, spot
# On a Mac that builds tiles (docs/MAC_RUNNER.md); everything lives in the checkout, no root:
#   scripts/aws_pipeline_setup.sh mac-install       venv, packages, public inputs, two LaunchAgents; starts
#   scripts/aws_pipeline_setup.sh mac-stop          finish the tile in hand, stop, publish "inactive"
#   scripts/aws_pipeline_setup.sh status
set -euo pipefail

REPO_URL=https://github.com/EricSpencer00/earthcraft
MAC=0; [ "$(uname -s)" = Darwin ] && MAC=1
if [ "$MAC" = 1 ]; then   # a Mac runner keeps code, settings and token under the user's own checkout
  ROOT=${EARTHCRAFT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}
  ENV_FILE=${EARTHCRAFT_ENV_FILE:-$HOME/.config/earthcraft/pipeline.env}
else
  ROOT=/opt/earthcraft
  ENV_FILE=/etc/earthcraft/pipeline.env
fi
REGION=${EARTHCRAFT_AWS_REGION:-us-west-2}
CONTROL=$ROOT/runs/regional/control
FRAME=$ROOT/runs/chicago-adaptation-city-001/frame.json
JAR=$ROOT/vendor/minecraft-client-1.21.10.jar
PY=$ROOT/.venv/bin/python
REQS=(requirements-chicago.txt requirements-lidar.txt requirements-footprints.txt)
EXTRA_PKGS=(huggingface_hub==1.8.0 hf_xet boto3)
SVC_USER=ubuntu
[ "$MAC" = 1 ] && SVC_USER=$(id -un)

[ -f "$ENV_FILE" ] && . "$ENV_FILE"
BRANCH=${EARTHCRAFT_BRANCH:-main}
# aws: token from SSM, inputs from S3. oci: both copied by push. local (a Mac): token file, public inputs
PROVIDER=${EARTHCRAFT_PROVIDER:-$([ "$MAC" = 1 ] && echo local || echo aws)}
TOKEN_FILE=${EARTHCRAFT_TOKEN_FILE:-$(dirname "$ENV_FILE")/hf_token}
AGENT=com.earthcraft.pipeline

as_user() { if [ "$(id -un)" = "$SVC_USER" ]; then "$@"; else runuser -u "$SVC_USER" -- "$@"; fi; }

install_requirements() {
  local reqs=() r
  for r in "${REQS[@]}"; do [ -f "$ROOT/$r" ] && reqs+=(-r "$r"); done   # skip files a branch does not have
  as_user "$PY" -m pip install -q -U pip
  (cd "$ROOT" && as_user "$PY" -m pip install -q "${reqs[@]}" "${EXTRA_PKGS[@]}")
}

bootstrap() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3-venv python3-dev git build-essential sqlite3 curl >/dev/null
  # rasterio 1.4.3 has no aarch64 wheel (Oracle Ampere): pip builds it against the system GDAL.
  [ "$(uname -m)" = aarch64 ] && apt-get install -y -qq libgdal-dev gdal-bin >/dev/null
  mkdir -p "$ROOT" /etc/earthcraft && chown "$SVC_USER:$SVC_USER" "$ROOT"
  [ -f "$ENV_FILE" ] || printf 'EARTHCRAFT_BRANCH=main\nEARTHCRAFT_WORKERS=4\n' > "$ENV_FILE"
  if [ ! -d "$ROOT/.git" ]; then as_user git clone -q -b "$BRANCH" "$REPO_URL" "$ROOT"; fi
  [ -x "$PY" ] || as_user python3 -m venv "$ROOT/.venv"
  install_requirements
  as_user mkdir -p "$CONTROL" "$(dirname "$FRAME")" "$ROOT/vendor" "$ROOT/runs/status"
  chmod 700 "$CONTROL" "$ROOT/vendor"
  install -m 0755 "$ROOT/scripts/aws_pipeline_setup.sh" /usr/local/sbin/earthcraft-pipeline
  ln -sf /usr/local/sbin/earthcraft-pipeline /usr/local/bin/earthcraft-status
  for unit in earthcraft-pipeline.service earthcraft-update.service earthcraft-update.timer \
              earthcraft-status.service earthcraft-status.timer \
              earthcraft-validate.service earthcraft-validate.timer; do
    install -m 0644 "$ROOT/configs/aws/$unit" /etc/systemd/system/
  done
  memory_dropin
  systemctl daemon-reload
  systemctl enable earthcraft-update.timer earthcraft-status.timer earthcraft-validate.timer earthcraft-pipeline.service
  systemctl start earthcraft-update.timer earthcraft-status.timer earthcraft-validate.timer
  # Control state: keep what exists; otherwise seed from the archived worker on the dataset.
  if [ ! -f "$CONTROL/plan.json" ] && [ -f "$FRAME" ]; then
    as_user "$PY" "$ROOT/scripts/regional_generate.py" --control "$CONTROL" --frame "$FRAME" --min-free 10GiB --seed-control
  fi
  systemctl start earthcraft-pipeline.service || true   # waits on ConditionPathExists for private inputs
}

memory_dropin() {   # the unit's MemoryMax=7G fits an m5a.large; a bigger box raises it in pipeline.env
  local d=/etc/systemd/system/earthcraft-pipeline.service.d
  if [ -n "${EARTHCRAFT_MEMORY_MAX:-}" ]; then
    mkdir -p "$d"; printf '[Service]\nMemoryMax=%s\n' "$EARTHCRAFT_MEMORY_MAX" > "$d/memory.conf"
  else
    rm -f "$d/memory.conf"
  fi
}

hf_token() {
  if [ "$PROVIDER" != aws ]; then
    [ -r "$TOKEN_FILE" ] || { echo "no $TOKEN_FILE: run 'aws_pipeline_setup.sh push <host>' from the coordinator" >&2; return 1; }
    tr -d '[:space:]' < "$TOKEN_FILE"; return
  fi
  "$PY" - "$REGION" <<'PY'
import sys, boto3
value = boto3.client('ssm', region_name=sys.argv[1]).get_parameter(
    Name='/earthcraft/hf_token', WithDecryption=True)['Parameter']['Value']
sys.stdout.write(value.strip())
PY
}

fetch_public() {   # the same inputs a contributor gets: plan and player-free templates from the dataset, JAR from Mojang
  mkdir -p "$CONTROL" "$(dirname "$FRAME")" "$(dirname "$JAR")"
  JAR="$JAR" FRAME="$FRAME" CONTROL="$CONTROL" PYTHONPATH="$ROOT/scripts" "$PY" - <<'PYEOF'
import hashlib, json, os
from pathlib import Path
import contribute, contributions, clean_templates
from hf_store import DEFAULT_REPO, DatasetReader
jar, frame, control = Path(os.environ['JAR']), Path(os.environ['FRAME']), Path(os.environ['CONTROL'])
reader = DatasetReader(DEFAULT_REPO)
if not jar.is_file():
    contribute.download_client_jar(jar)
if hashlib.sha1(jar.read_bytes()).hexdigest() != contributions.CLIENT_SHA1:
    raise SystemExit(f'{jar} is not the official {contributions.CLIENT_VERSION} client.jar')
if not frame.is_file():
    plan = control / 'plan.json'
    if not plan.exists():
        reader.download(f'{contribute.LEGACY_CONTROL}/plan.json', plan)
    frame.write_text(json.dumps(json.loads(plan.read_text())['frame']))
if not (control / 'templates').is_dir():
    clean_templates.fetch(control / 'templates', reader)
PYEOF
}

fetch_private() {   # client JAR, world templates and frame from the private S3 bucket, via the instance role
  [ -f "$JAR" ] && [ -f "$FRAME" ] && [ -d "$CONTROL/templates" ] && return 0
  if [ "$PROVIDER" = local ]; then fetch_public; return; fi
  [ "$PROVIDER" = aws ] || { echo "private inputs missing: run 'aws_pipeline_setup.sh push <host>' from the coordinator" >&2; return 1; }
  BUCKET=${EARTHCRAFT_PRIVATE_BUCKET:-earthcraft-pipeline-private-971966099666} \
  JAR="$JAR" FRAME="$FRAME" TEMPLATES="$CONTROL/templates" "$PY" - <<'PYEOF'
import os, boto3
s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-west-2'))
bucket = os.environ['BUCKET']
targets = {'private/minecraft-client-1.21.10.jar': os.environ['JAR'], 'private/frame.json': os.environ['FRAME']}
for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix='private/templates/'):
    for item in page.get('Contents', []):
        targets[item['Key']] = os.path.join(os.environ['TEMPLATES'], item['Key'][len('private/templates/'):])
for key, path in targets.items():
    if os.path.exists(path):
        continue
    os.makedirs(os.path.dirname(path), exist_ok=True)
    s3.download_file(bucket, key, path + '.part')
    os.replace(path + '.part', path)
    print('fetched', key)
PYEOF
}

shard() {   # this box's share of the plan: instance tag EarthcraftShard (set by launch), else pipeline.env
  local tag; tag=$(imds tags/instance/EarthcraftShard)
  case "$tag" in */*) echo "$tag" ;; *) echo "${EARTHCRAFT_SHARD:-}" ;; esac
}

run() {
  cd "$ROOT"
  fetch_private
  EARTHCRAFT_SHARD=$(shard); export EARTHCRAFT_SHARD
  mkdir -p /run/earthcraft 2>/dev/null && echo "$EARTHCRAFT_SHARD" > /run/earthcraft/shard 2>/dev/null || true
  HF_TOKEN=$(hf_token); export HF_TOKEN
  export EARTHCRAFT_CLIENT_JAR=$JAR EARTHCRAFT_HF_PYTHON=$PY
  export EARTHCRAFT_STATUS_FILE=$ROOT/runs/status/pipeline.json
  export EARTHCRAFT_STOP_TIMEOUT=${EARTHCRAFT_STOP_TIMEOUT:-1800}
  [ -f "$CONTROL/jobs.sqlite" ] || "$PY" scripts/regional_generate.py --control "$CONTROL" --frame "$FRAME" \
      --min-free 10GiB --seed-control
  if [ "$MAC" = 1 ]; then
    mkdir -p "$ROOT/runs/status"
    caffeinate -i -w $$ &   # no idle sleep while the supervisor (this PID after exec) lives
  fi
  # Source cache: ~10 GiB of way/footprint indexes plus room for ~8 inflated ArcGrid strips (1.2 GB each).
  # Footprint = cache + per worker (3 GiB tile + 1 GiB batch + 1 GiB upload scratch) + control.
  local workers=${EARTHCRAFT_WORKERS:-1} cache_gib=${EARTHCRAFT_CACHE_GIB:-20}
  exec "$PY" -u scripts/regional_supervisor.py --control "$CONTROL" --frame "$FRAME" \
    --scratch "$ROOT/runs/scratch" --hf-python "$PY" --footprints $( [ "${EARTHCRAFT_TREES:-1}" = 0 ] || echo --trees) $( [ "${EARTHCRAFT_FACADES:-1}" = 0 ] || echo --facades) $( [ "${EARTHCRAFT_GROUND:-1}" = 0 ] || echo --ground) --workers "$workers" --lane "${EARTHCRAFT_LANE:-auto}" \
    --cache-cap "${cache_gib}GiB" --footprint-cap "${EARTHCRAFT_FOOTPRINT_CAP:-$((cache_gib + 5 * workers + 1))GiB}" \
    --min-free "${EARTHCRAFT_MIN_FREE:-10GiB}" --tile-cap 3GiB --batch-tiles "${EARTHCRAFT_BATCH_TILES:-24}"
}

update() {
  cd "$ROOT"
  local branch=$BRANCH
  if ! as_user git fetch -q origin "$branch" 2>/dev/null; then
    branch=main; as_user git fetch -q origin main     # a merged-and-deleted feature branch falls back to main
  fi
  # Compare against the last fully deployed commit, not HEAD: a deploy that failed halfway
  # (after the fast-forward) is finished on the next tick instead of being skipped forever.
  # No marker means no deploy was ever recorded: redeploy everything once (falling back to
  # HEAD would skip a box whose HEAD was fast-forwarded by a failed tick). The marker is
  # written only after the restart succeeded. Verified in specs/Deploy.tla (mode "fixed").
  local old new deployed=/var/lib/earthcraft/deployed
  old=$(cat "$deployed" 2>/dev/null || echo none); new=$(as_user git rev-parse FETCH_HEAD)
  if [ "$old" = "$new" ]; then
    if [ -f /run/earthcraft/shard ] && [ "$(cat /run/earthcraft/shard)" != "$(shard)" ]; then
      echo "shard changed to $(shard); restarting (the worker finishes its tile and commits its batch first)"
      systemctl restart earthcraft-pipeline.service
    fi
    exit 0
  fi
  if [ "$(as_user git rev-parse --abbrev-ref HEAD)" != "$branch" ]; then
    as_user git checkout -q -B "$branch" FETCH_HEAD
  else
    as_user git merge -q --ff-only FETCH_HEAD
  fi
  changed() { [ "$old" = none ] || ! as_user git cat-file -e "$old^{commit}" 2>/dev/null || ! as_user git diff --quiet "$old" "$new" -- "$@"; }
  local restart=0 workers
  # Only scripts the workers run; site-only edits must not drain the generator.
  workers=$("$PY" scripts/worker_paths.py 2>/dev/null) || workers=scripts
  # shellcheck disable=SC2086
  if changed $workers scripts/worker_paths.py 'requirements*.txt' configs/aws || \
     ! cmp -s scripts/aws_pipeline_setup.sh /usr/local/sbin/earthcraft-pipeline || \
     { [ -f /run/earthcraft/shard ] && [ "$(cat /run/earthcraft/shard)" != "$(shard)" ]; }; then
    restart=1
  fi
  if changed 'requirements*.txt'; then install_requirements; fi
  if changed configs/aws scripts/aws_pipeline_setup.sh || ! cmp -s scripts/aws_pipeline_setup.sh /usr/local/sbin/earthcraft-pipeline; then
    install -m 0755 scripts/aws_pipeline_setup.sh /usr/local/sbin/earthcraft-pipeline
    install -m 0644 configs/aws/*.service configs/aws/*.timer /etc/systemd/system/
    memory_dropin
    systemctl daemon-reload
    for timer in configs/aws/*.timer; do systemctl enable --now "$(basename "$timer")" 2>/dev/null || true; done
  fi
  if [ "$restart" = 1 ]; then
    echo "updated $old -> $new; restarting (SIGTERM: the worker finishes its tile and commits its batch first)"
    systemctl restart earthcraft-pipeline.service
  else
    echo "updated $old -> $new; no worker changes, keeping the generator running"
  fi
  mkdir -p "$(dirname "$deployed")"; echo "$new" > "$deployed.tmp"; mv "$deployed.tmp" "$deployed"
}

validate() {   # timer: comment PASS/FAIL on open contribution pull requests; the owner merges
  cd "$ROOT"
  HF_TOKEN=$(hf_token); export HF_TOKEN
  export EARTHCRAFT_CLIENT_JAR=$JAR PYTHONPATH=$ROOT/scripts
  # The spot check shares the pipeline's source cache (same cap as the pipeline, see run), so it adds no second copy of the indexes.
  "$PY" -u scripts/validate_contributions.py --spot-workdir "$ROOT/runs/scratch/validate" \
    --spot-cache-dir "$ROOT/runs/scratch/cache" --spot-cache-cap "${EARTHCRAFT_CACHE_GIB:-20}GiB" "$@"
}

oci_md() {   # Oracle instance metadata (v2); empty off Oracle
  [ "$PROVIDER" = oci ] || return 0
  curl -sf -m 2 -H 'Authorization: Bearer Oracle' "http://169.254.169.254/opc/v2/$1" || true
}

imds() {
  [ "$PROVIDER" = aws ] || return 0
  local t; t=$(curl -sf -m 2 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60') || return 0
  curl -sf -m 2 -H "X-aws-ec2-metadata-token: $t" "http://169.254.169.254/latest/meta-data/$1" || true
}

service_state() {   # systemd's words on every host: active, inactive or failed
  if [ "$MAC" = 1 ]; then
    if launchctl print "gui/$(id -u)/$AGENT" 2>/dev/null | grep -q 'state = running'; then echo active; else echo inactive; fi
  else
    systemctl is-active earthcraft-pipeline 2>/dev/null || true
  fi
}

status_python() { if [ -x "$PY" ]; then echo "$PY"; else echo python3; fi; }

status() {
  local out=${2:-}
  [ "${1:-}" = "--json" ] || out=""
  CONTROL=$CONTROL ROOT=$ROOT PROVIDER=$PROVIDER OCI=$(oci_md instance/) LIFECYCLE=$(imds instance-life-cycle) ACTION=$(imds spot/instance-action) \
  INSTANCE=$(imds instance-id) AZ=$(imds placement/availability-zone) OUT=$out SHARD=$(shard) \
  WORKERS=${EARTHCRAFT_WORKERS:-1} \
  HOST_LABEL=${EARTHCRAFT_HOST_LABEL:-mac} \
  SERVICE=$(service_state) "$(status_python)" - <<'PY'
import base64, json, os, re, shutil, sqlite3, subprocess, time, zlib
from datetime import datetime
from pathlib import Path
control, root = Path(os.environ['CONTROL']), Path(os.environ['ROOT'])
now = time.time()
DAY = 86400


def recent_events(log, since, block=1 << 20):
    """Events newer than `since`, reading the log backwards: logs only grow, so the cost
    stays proportional to one day of work, not the whole history."""
    events, size = [], log.stat().st_size
    with open(log, 'rb') as f:
        end, tail = size, b''
        while end > 0:
            start = max(0, end - block)
            f.seek(start)
            lines = (f.read(end - start) + tail).split(b'\n')
            tail, end = (lines.pop(0) if start else b''), start
            older = False
            for line in reversed(lines):
                if not line.startswith(b'{'):
                    continue
                try:
                    event = json.loads(line)
                    at = datetime.fromisoformat(event['utc']).timestamp()
                except (ValueError, KeyError, TypeError):
                    continue
                if at < since:
                    older = True
                    break
                events.append((at, event))
            if older:
                break
    return events


events = [e for log in sorted((control / 'logs').glob('*.log')) for e in recent_events(log, now - DAY)]
published = sorted(((at, e.get('tile')) for at, e in events if e.get('event') == 'tile_published'), key=lambda item: item[0])
commits = sorted(((at, e) for at, e in events if e.get('event') == 'commit_verified'), key=lambda item: item[0])
last_commit = ({'oid': commits[-1][1].get('oid'), 'utc': commits[-1][1].get('utc'), 'label': commits[-1][1].get('label')}
               if commits else None)
hour = sum(1 for at, _ in published if at >= now - 3600)
day = len(published)
journal, tiles, detailed = {}, [], []
try:
    with sqlite3.connect(f'file:{control / "jobs.sqlite"}?mode=ro', uri=True, timeout=20) as db:
        for stage, state, n in db.execute('SELECT stage,state,count(*) FROM jobs GROUP BY 1,2'):
            journal.setdefault(str(stage), {})[state] = n
        tiles = [t for (t,) in db.execute("SELECT tile FROM jobs WHERE stage=1 AND state='complete' "
                                           "AND evidence LIKE 'tiles/v2/%'")]
        detailed = [t for (t,) in db.execute("SELECT tile FROM jobs WHERE stage=2 AND state='complete' "
                                              "AND evidence LIKE 'tiles/v2/%'")]
except sqlite3.Error as error:
    journal = {'error': str(error)}


def bitmap(ids):
    """tiles/v2 tiles as a zlib bitmap over their bounding box (a few KB for the whole plan).
    The site adds published tiles newer than its base map snapshot from published_tiles, and
    colours the detail layer from detailed_tiles (surface detail: scans, trees, facades, ground)."""
    xy = [tuple(map(int, t.split('_'))) for t in ids if re.fullmatch(r'-?\d+_-?\d+', t)]
    if not xy:
        return None
    x0, z0 = min(x for x, _ in xy), min(z for _, z in xy)
    width, height = max(x for x, _ in xy) - x0 + 1, max(z for _, z in xy) - z0 + 1
    bits = bytearray((width * height + 7) // 8)
    for x, z in xy:
        i = (z - z0) * width + (x - x0)
        bits[i >> 3] |= 1 << (i & 7)
    return {'x0': x0, 'z0': z0, 'width': width, 'height': height, 'count': len(xy),
            'encoding': 'base64(zlib(bitmap)); bit i = (z - z0) * width + (x - x0), least significant bit first',
            'bitmap': base64.b64encode(zlib.compress(bytes(bits), 9)).decode()}


def host_info():
    if os.environ['PROVIDER'] == 'local':
        return {'provider': 'local', 'instance': os.environ['HOST_LABEL'], 'az': '', 'lifecycle': 'owner-machine',
                'spot_interruption': None}
    if os.environ['PROVIDER'] == 'oci':
        try:
            md = json.loads(os.environ['OCI'] or '{}')
        except ValueError:
            md = {}
        return {'provider': 'oci', 'instance': md.get('displayName') or md.get('id', ''),
                'az': md.get('availabilityDomain', ''), 'lifecycle': md.get('shape', ''), 'spot_interruption': None}
    return {'provider': 'aws-ec2', 'instance': os.environ['INSTANCE'], 'az': os.environ['AZ'],
            'lifecycle': os.environ['LIFECYCLE'], 'spot_interruption': os.environ['ACTION'] or None}


usage = shutil.disk_usage(root)
scratch = sum(f.stat().st_size for f in (root / 'runs/scratch/gen').rglob('*') if f.is_file()) \
    if (root / 'runs/scratch/gen').exists() else 0
sha = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
result = {
    'schema': 'earthcraft.pipeline-status.v1', 'time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
    'host': host_info(),
    'service': os.environ['SERVICE'], 'git_sha': sha,
    'shard': os.environ['SHARD'] or None,
    'workers': int(os.environ['WORKERS']) if os.environ['SERVICE'] == 'active' else 0,  # none run while stopped
    'tiles_published_journal': len(tiles), 'tiles_last_hour': hour, 'tiles_last_24h': day,
    'tiles_per_hour_24h': round(day / 24, 2), 'tiles_complete_journal': journal.get('2', {}).get('complete'),
    'last_commit': last_commit, 'last_tile': published[-1][1] if published else None,
    'journal': journal, 'published_tiles': bitmap(tiles), 'detailed_tiles': bitmap(detailed),
    'disk': {'total_bytes': usage.total, 'free_bytes': usage.free, 'gen_scratch_bytes': scratch},
}
text = json.dumps(result, indent=1, sort_keys=True)
if os.environ['OUT']:
    target = Path(os.environ['OUT']); target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.tmp'); tmp.write_text(text + '\n'); tmp.replace(target)
else:
    gib = 1 << 30
    print(f"service {result['service']}  git {sha[:12]}  workers {result['workers']}  shard {result['shard'] or 'all'}  "
          f"{result['host']['instance']} {result['host']['az']} "
          f"({result['host']['lifecycle']}, interruption: {result['host']['spot_interruption'] or 'none'})")
    print(f"tiles: {hour} last hour, {day} last 24h, {len(tiles)} published in the journal; appearance complete: "
          f"{result['tiles_complete_journal']}")
    print(f"last commit: {last_commit['oid'][:12] + ' ' + last_commit['utc'] if last_commit else 'none'}")
    print(f"disk: {usage.free / gib:.1f} GiB free of {usage.total / gib:.1f}; tile scratch {scratch / gib:.2f} GiB")
PY
}

push() {   # on the coordinator: private inputs never go to the public repo or user-data
  local host=${1:-earthcraft-aws} mc="$HOME/Library/Application Support/minecraft/versions/1.21.10/1.21.10.jar"
  local src=${EARTHCRAFT_SRC:-$HOME/earthcraft}
  [ -f "${EARTHCRAFT_CLIENT_JAR:-}" ] && mc=$EARTHCRAFT_CLIENT_JAR
  ssh "$host" "sudo cloud-init status --wait >/dev/null 2>&1 || true; test -d $CONTROL"
  scp -q "$mc" "$host:$JAR"
  scp -q "$src/runs/chicago-adaptation-city-001/frame.json" "$host:$FRAME"
  COPYFILE_DISABLE=1 tar -C "$src/runs/regional/control" -cf - templates | ssh "$host" "tar -C $CONTROL -xf - 2>/dev/null"
  if ssh "$host" "grep -q '^EARTHCRAFT_PROVIDER=oci' /etc/earthcraft/pipeline.env"; then
    # No SSM there: the HF write token goes to a root-owned 0640 file the service user can read.
    local token=${HF_TOKEN_FILE:-$HOME/.cache/huggingface/token}
    [ -r "$token" ] || { echo "push: no HF token at $token (set HF_TOKEN_FILE)" >&2; return 1; }
    ssh "$host" "sudo install -m 0640 -o root -g $SVC_USER /dev/stdin $TOKEN_FILE" < "$token"
  fi
  ssh "$host" "sudo systemctl start earthcraft-pipeline && earthcraft-status"
}

launch() {   # on the coordinator: N more identical boxes from the existing key, SG and instance profile
  local aws=${AWS:-$HOME/.local/bin/aws} r="--region $REGION" count=1 dry="" ami sg ud tags ids existing total i id
  while [ $# -gt 0 ]; do
    case "$1" in
      --count) count=$2; shift 2 ;;
      --dry-run) dry=echo; shift ;;
      *) echo "launch: unknown option $1" >&2; return 64 ;;
    esac
  done
  case "$count" in ''|*[!0-9]*|0) echo "launch: --count needs a positive number" >&2; return 64 ;; esac
  ud=$(dirname "$0")/../configs/aws/cloud-init.yaml
  tags='{Key=Project,Value=earthcraft},{Key=Owner,Value=EricSpencer00},{Key=Name,Value=earthcraft-pipeline}'
  existing=$($aws $r ec2 describe-instances --filters Name=tag:Name,Values=earthcraft-pipeline \
    Name=instance-state-name,Values=pending,running,stopping,stopped \
    --query 'sort_by(Reservations[].Instances[], &LaunchTime)[].InstanceId' --output text 2>/dev/null || true)
  if [ -n "$dry" ]; then
    ami='<ubuntu-24.04-ami>'; sg='<earthcraft-pipeline-sg>'
  else
    ami=$($aws $r ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)
    sg=$($aws $r ec2 describe-security-groups --group-names earthcraft-pipeline --query 'SecurityGroups[0].GroupId' --output text)
  fi
  ids=$($dry $aws $r ec2 run-instances --count "$count" --image-id "$ami" --instance-type "${INSTANCE_TYPE:-m6a.large}" \
    --key-name earthcraft-pipeline --security-group-ids "$sg" --iam-instance-profile Name=earthcraft-pipeline \
    ${SUBNET:+--subnet-id $SUBNET} --associate-public-ip-address \
    --metadata-options HttpTokens=required,InstanceMetadataTags=enabled \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":60,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
    --instance-market-options '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"persistent","InstanceInterruptionBehavior":"stop"}}' \
    --user-data "file://$ud" \
    --tag-specifications "ResourceType=instance,Tags=[$tags]" "ResourceType=volume,Tags=[$tags]" \
      "ResourceType=spot-instances-request,Tags=[$tags]" "ResourceType=network-interface,Tags=[$tags]" \
    --query 'Instances[].InstanceId' --output text)
  [ -n "$dry" ] && { echo "$ids"; ids=$(seq -f 'i-new%g' 1 "$count" | tr '\n' ' '); }
  # Deterministic shares: existing boxes keep their launch order, new ones follow. Each box reads its tag at
  # service start; the update timer restarts a box whose tag changed (after its tile and batch finish).
  total=$(( $(echo $existing | wc -w) + count )); i=0
  for id in $existing $ids; do
    if [ -n "$dry" ]; then echo $aws $r ec2 modify-instance-metadata-options --instance-id "$id" --instance-metadata-tags enabled
    else $aws $r ec2 modify-instance-metadata-options --instance-id "$id" --instance-metadata-tags enabled >/dev/null; fi
    $dry $aws $r ec2 create-tags --resources "$id" --tags "Key=EarthcraftShard,Value=$i/$total"
    i=$((i + 1))
  done
  [ -n "$dry" ] && { echo "dry run: $count new box(es), $total shard(s); nothing launched"; return 0; }
  $aws $r ec2 wait instance-running --instance-ids $ids
  for id in $ids; do
    echo "launched $id at $($aws $r ec2 describe-instances --instance-ids "$id" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
  done
  echo "each box bootstraps itself (private inputs from S3) and claims only its shard; check with: ssh <ip> earthcraft-status"
}

mac_agent() {   # write one LaunchAgent: label, seconds between runs (0 = keep alive), then the command
  local label=$1 every=$2 plist; shift 2
  plist=$HOME/Library/LaunchAgents/$label.plist
  mkdir -p "$(dirname "$plist")" "$ROOT/runs/regional/logs"
  {
    printf '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    printf '<plist version="1.0"><dict>\n<key>Label</key><string>%s</string>\n<key>ProgramArguments</key><array>\n' "$label"
    local arg; for arg in "$@"; do printf '<string>%s</string>\n' "$arg"; done
    printf '</array>\n<key>WorkingDirectory</key><string>%s</string>\n' "$ROOT"
    printf '<key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>\n'
    # Standard, not Background: the Background type also throttles networking (Hub downloads fell to 0.3 MB/s).
    printf '<key>ProcessType</key><string>Standard</string>\n<key>Nice</key><integer>10</integer>\n<key>LowPriorityIO</key><true/>\n'
    if [ "$every" = 0 ]; then
      printf '<key>RunAtLoad</key><true/>\n<key>KeepAlive</key><true/>\n<key>ThrottleInterval</key><integer>60</integer>\n'
      printf '<key>ExitTimeOut</key><integer>%s</integer>\n' "${EARTHCRAFT_STOP_TIMEOUT:-1800}"
    else
      printf '<key>RunAtLoad</key><true/>\n<key>StartInterval</key><integer>%s</integer>\n' "$every"
    fi
    printf '<key>StandardOutPath</key><string>%s/runs/regional/logs/%s.log</string>\n' "$ROOT" "$label"
    printf '<key>StandardErrorPath</key><string>%s/runs/regional/logs/%s.log</string>\n</dict></plist>\n' "$ROOT" "$label"
  } > "$plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
}

mac_install() {
  [ "$MAC" = 1 ] || { echo "mac-install runs on macOS" >&2; return 64; }
  [ -r "$TOKEN_FILE" ] || { echo "no Hugging Face write token at $TOKEN_FILE (mode 600)" >&2; return 1; }
  cd "$ROOT"
  mkdir -p "$(dirname "$ENV_FILE")"
  [ -f "$ENV_FILE" ] || printf 'EARTHCRAFT_WORKERS=2\nEARTHCRAFT_LANE=quality\nEARTHCRAFT_BATCH_TILES=8\n' > "$ENV_FILE"
  if [ ! -x "$PY" ]; then
    local python; python=$(command -v python3.11 || command -v python3.12 || command -v python3)
    "$python" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || { echo "Python 3.11+ is needed" >&2; return 1; }
    "$python" -m venv "$ROOT/.venv"
  fi
  install_requirements
  fetch_private
  local self=$ROOT/scripts/aws_pipeline_setup.sh
  mac_agent "$AGENT" 0 /bin/bash "$self" run
  mac_agent "$AGENT.status" 900 /bin/bash "$self" status --json "$ROOT/runs/status/pipeline.json"
  sleep 5; status
}

mac_stop() {   # SIGTERM: the supervisor's workers finish their tile and commit their batch
  [ "$MAC" = 1 ] || { echo "mac-stop runs on macOS" >&2; return 64; }
  launchctl bootout "gui/$(id -u)/$AGENT" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)/$AGENT.status" 2>/dev/null || true
  local waited=0
  while pgrep -f "$ROOT/scripts/regional_supervisor.py" >/dev/null && [ "$waited" -lt "${EARTHCRAFT_STOP_TIMEOUT:-1800}" ]; do
    sleep 5; waited=$((waited + 5))
  done
  # Say so on the dataset: the monitor reads "inactive" as a deliberate stop, not an outage.
  cd "$ROOT"
  local scratch=$ROOT/runs/scratch/status
  rm -rf "$scratch"; mkdir -p "$scratch/src" "$ROOT/runs/status"
  status --json "$scratch/src/pipeline.json"
  cp "$scratch/src/pipeline.json" "$ROOT/runs/status/pipeline.json"
  HF_TOKEN=$(hf_token) "$PY" scripts/hf_store.py --scratch "$scratch/hf" --cap 1GiB --min-free 2GiB \
    push-files --src "$scratch/src" --dest status
  rm -rf "$scratch"
  status
}

cmd=${1:-}; shift || true
case "$cmd" in
  mac-install) mac_install "$@" ;;
  mac-stop) mac_stop "$@" ;;
  bootstrap|run|update|launch|push|validate) "$cmd" "$@" ;;
  status) status "$@" ;;
  *) if [ "$(basename "$0")" = earthcraft-status ]; then status "$cmd" "$@"; else sed -n 2,12p "$0"; exit 64; fi ;;
esac
