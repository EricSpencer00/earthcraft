#!/usr/bin/env bash
# Earthcraft always-on pipeline host (AWS EC2, Ubuntu 24.04). See docs/AWS_PIPELINE.md.
#
# On the coordinator (MacBook, has the AWS CLI, the ssh alias and the private files):
#   scripts/aws_pipeline_setup.sh launch            new spot instance with cloud-init, then `push`
#   scripts/aws_pipeline_setup.sh push [HOST]       copy private inputs (client JAR, templates, frame), start
# On the instance (cloud-init runs `bootstrap` as root; the units call the rest):
#   aws_pipeline_setup.sh bootstrap                 packages, checkout, venv, units, control seed
#   aws_pipeline_setup.sh run                       service entry: token from SSM, exec the supervisor
#   aws_pipeline_setup.sh update                    timer: fast-forward, reinstall, graceful restart
#   aws_pipeline_setup.sh status [--json FILE]      tiles done, tiles/hour, last commit, disk, spot
set -euo pipefail

REPO_URL=https://github.com/EricSpencer00/earthcraft
ROOT=/opt/earthcraft
ENV_FILE=/etc/earthcraft/pipeline.env
REGION=${EARTHCRAFT_AWS_REGION:-us-west-2}
CONTROL=$ROOT/runs/regional/control
FRAME=$ROOT/runs/chicago-adaptation-city-001/frame.json
JAR=$ROOT/vendor/minecraft-client-1.21.10.jar
PY=$ROOT/.venv/bin/python
REQS=(requirements-chicago.txt requirements-lidar.txt requirements-footprints.txt)
EXTRA_PKGS=(huggingface_hub==1.8.0 hf_xet boto3)
SVC_USER=ubuntu

[ -f "$ENV_FILE" ] && . "$ENV_FILE"
BRANCH=${EARTHCRAFT_BRANCH:-main}

as_user() { if [ "$(id -un)" = "$SVC_USER" ]; then "$@"; else runuser -u "$SVC_USER" -- "$@"; fi; }

install_requirements() {
  as_user "$PY" -m pip install -q -U pip
  (cd "$ROOT" && as_user "$PY" -m pip install -q $(printf -- '-r %s ' "${REQS[@]}") "${EXTRA_PKGS[@]}")
}

bootstrap() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3-venv python3-dev git build-essential sqlite3 curl >/dev/null
  mkdir -p "$ROOT" /etc/earthcraft && chown "$SVC_USER:$SVC_USER" "$ROOT"
  [ -f "$ENV_FILE" ] || printf 'EARTHCRAFT_BRANCH=main\n' > "$ENV_FILE"
  if [ ! -d "$ROOT/.git" ]; then as_user git clone -q -b "$BRANCH" "$REPO_URL" "$ROOT"; fi
  [ -x "$PY" ] || as_user python3 -m venv "$ROOT/.venv"
  install_requirements
  as_user mkdir -p "$CONTROL" "$(dirname "$FRAME")" "$ROOT/vendor" "$ROOT/runs/status"
  chmod 700 "$CONTROL" "$ROOT/vendor"
  install -m 0755 "$ROOT/scripts/aws_pipeline_setup.sh" /usr/local/sbin/earthcraft-pipeline
  ln -sf /usr/local/sbin/earthcraft-pipeline /usr/local/bin/earthcraft-status
  for unit in earthcraft-pipeline.service earthcraft-update.service earthcraft-update.timer \
              earthcraft-status.service earthcraft-status.timer; do
    install -m 0644 "$ROOT/configs/aws/$unit" /etc/systemd/system/
  done
  systemctl daemon-reload
  systemctl enable earthcraft-update.timer earthcraft-status.timer earthcraft-pipeline.service
  systemctl start earthcraft-update.timer earthcraft-status.timer
  # Control state: keep what exists; otherwise seed from the archived worker on the dataset.
  if [ ! -f "$CONTROL/plan.json" ] && [ -f "$FRAME" ]; then
    as_user "$PY" "$ROOT/scripts/regional_generate.py" --control "$CONTROL" --frame "$FRAME" --min-free 10GiB --seed-control
  fi
  systemctl start earthcraft-pipeline.service || true   # waits on ConditionPathExists for private inputs
}

hf_token() {
  "$PY" - "$REGION" <<'PY'
import sys, boto3
value = boto3.client('ssm', region_name=sys.argv[1]).get_parameter(
    Name='/earthcraft/hf_token', WithDecryption=True)['Parameter']['Value']
sys.stdout.write(value.strip())
PY
}

run() {
  cd "$ROOT"
  HF_TOKEN=$(hf_token); export HF_TOKEN
  export EARTHCRAFT_CLIENT_JAR=$JAR EARTHCRAFT_HF_PYTHON=$PY
  export EARTHCRAFT_STATUS_FILE=$ROOT/runs/status/pipeline.json
  export EARTHCRAFT_STOP_TIMEOUT=${EARTHCRAFT_STOP_TIMEOUT:-1800}
  [ -f "$CONTROL/plan.json" ] || "$PY" scripts/regional_generate.py --control "$CONTROL" --frame "$FRAME" \
      --min-free 10GiB --seed-control
  exec "$PY" -u scripts/regional_supervisor.py --control "$CONTROL" --frame "$FRAME" \
    --scratch "$ROOT/runs/scratch" --hf-python "$PY" --footprints --workers "${EARTHCRAFT_WORKERS:-1}" \
    --cache-cap "${EARTHCRAFT_CACHE_CAP:-12GiB}" --footprint-cap "${EARTHCRAFT_FOOTPRINT_CAP:-34GiB}" \
    --min-free "${EARTHCRAFT_MIN_FREE:-10GiB}" --tile-cap 3GiB --batch-tiles "${EARTHCRAFT_BATCH_TILES:-8}"
}

update() {
  cd "$ROOT"
  local branch=$BRANCH
  if ! as_user git fetch -q origin "$branch" 2>/dev/null; then
    branch=main; as_user git fetch -q origin main     # a merged-and-deleted feature branch falls back to main
  fi
  local old new
  old=$(as_user git rev-parse HEAD); new=$(as_user git rev-parse FETCH_HEAD)
  [ "$old" = "$new" ] && exit 0
  if [ "$(as_user git rev-parse --abbrev-ref HEAD)" != "$branch" ]; then
    as_user git checkout -q -B "$branch" FETCH_HEAD
  else
    as_user git merge -q --ff-only FETCH_HEAD
  fi
  if ! as_user git diff --quiet "$old" "$new" -- 'requirements*.txt'; then install_requirements; fi
  if ! as_user git diff --quiet "$old" "$new" -- configs/aws scripts/aws_pipeline_setup.sh; then
    install -m 0755 scripts/aws_pipeline_setup.sh /usr/local/sbin/earthcraft-pipeline
    install -m 0644 configs/aws/*.service configs/aws/*.timer /etc/systemd/system/
    systemctl daemon-reload
  fi
  echo "updated $old -> $new; restarting (SIGTERM: the worker finishes its tile and commits its batch first)"
  systemctl restart earthcraft-pipeline.service
}

imds() {
  local t; t=$(curl -sf -m 2 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60') || return 0
  curl -sf -m 2 -H "X-aws-ec2-metadata-token: $t" "http://169.254.169.254/latest/meta-data/$1" || true
}

status() {
  local out=${2:-}
  [ "${1:-}" = "--json" ] || out=""
  CONTROL=$CONTROL ROOT=$ROOT LIFECYCLE=$(imds instance-life-cycle) ACTION=$(imds spot/instance-action) \
  INSTANCE=$(imds instance-id) AZ=$(imds placement/availability-zone) OUT=$out \
  SERVICE=$(systemctl is-active earthcraft-pipeline 2>/dev/null || true) python3 - <<'PY'
import json, os, shutil, sqlite3, subprocess, time
from pathlib import Path
control, root = Path(os.environ['CONTROL']), Path(os.environ['ROOT'])
now = time.time()
published, last_commit = [], None
for log in sorted((control / 'logs').glob('*.log*')):
    for line in log.read_text(errors='replace').splitlines():
        if not line.startswith('{'):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('event') == 'tile_published':
            published.append((event.get('utc'), event.get('tile')))
        elif event.get('event') == 'commit_verified':
            if last_commit is None or event.get('utc', '') > last_commit['utc']:
                last_commit = {'oid': event.get('oid'), 'utc': event.get('utc'), 'label': event.get('label')}
def age(utc):
    from datetime import datetime
    return now - datetime.fromisoformat(utc).timestamp()
hour = sum(1 for utc, _ in published if utc and age(utc) <= 3600)
day = sum(1 for utc, _ in published if utc and age(utc) <= 86400)
journal = {}
try:
    with sqlite3.connect(f'file:{control / "jobs.sqlite"}?mode=ro', uri=True, timeout=20) as db:
        for stage, state, n in db.execute('SELECT stage,state,count(*) FROM jobs GROUP BY 1,2'):
            journal.setdefault(str(stage), {})[state] = n
except sqlite3.Error as error:
    journal = {'error': str(error)}
usage = shutil.disk_usage(root)
scratch = sum(f.stat().st_size for f in (root / 'runs/scratch/gen').rglob('*') if f.is_file()) \
    if (root / 'runs/scratch/gen').exists() else 0
sha = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
result = {
    'schema': 'earthcraft.pipeline-status.v1', 'time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
    'host': {'provider': 'aws-ec2', 'instance': os.environ['INSTANCE'], 'az': os.environ['AZ'],
             'lifecycle': os.environ['LIFECYCLE'], 'spot_interruption': os.environ['ACTION'] or None},
    'service': os.environ['SERVICE'], 'git_sha': sha,
    'tiles_published_logged': len(published), 'tiles_last_hour': hour, 'tiles_last_24h': day,
    'tiles_per_hour_24h': round(day / 24, 2), 'tiles_complete_journal': journal.get('2', {}).get('complete'),
    'last_commit': last_commit, 'last_tile': published[-1][1] if published else None,
    'journal': journal,
    'disk': {'total_bytes': usage.total, 'free_bytes': usage.free, 'gen_scratch_bytes': scratch},
}
text = json.dumps(result, indent=1, sort_keys=True)
if os.environ['OUT']:
    target = Path(os.environ['OUT']); target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.tmp'); tmp.write_text(text + '\n'); tmp.replace(target)
else:
    gib = 1 << 30
    print(f"service {result['service']}  git {sha[:12]}  {result['host']['instance']} {result['host']['az']} "
          f"({result['host']['lifecycle']}, interruption: {result['host']['spot_interruption'] or 'none'})")
    print(f"tiles: {hour} last hour, {day} last 24h, {len(published)} logged here; appearance complete in journal: "
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
  ssh "$host" "sudo systemctl start earthcraft-pipeline && earthcraft-status"
}

launch() {   # on the coordinator: replacement instance from the existing key, SG and instance profile
  local aws=${AWS:-$HOME/.local/bin/aws} r="--region $REGION" ami sg ud tags id ip
  ami=$($aws $r ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)
  sg=$($aws $r ec2 describe-security-groups --group-names earthcraft-pipeline --query 'SecurityGroups[0].GroupId' --output text)
  ud=$(dirname "$0")/../configs/aws/cloud-init.yaml
  tags='{Key=Project,Value=earthcraft},{Key=Owner,Value=EricSpencer00},{Key=Name,Value=earthcraft-pipeline}'
  id=$($aws $r ec2 run-instances --image-id "$ami" --instance-type "${INSTANCE_TYPE:-m6a.large}" \
    --key-name earthcraft-pipeline --security-group-ids "$sg" --iam-instance-profile Name=earthcraft-pipeline \
    ${SUBNET:+--subnet-id $SUBNET} --associate-public-ip-address --metadata-options HttpTokens=required \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":60,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
    --instance-market-options '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"persistent","InstanceInterruptionBehavior":"stop"}}' \
    --user-data "file://$ud" \
    --tag-specifications "ResourceType=instance,Tags=[$tags]" "ResourceType=volume,Tags=[$tags]" \
      "ResourceType=spot-instances-request,Tags=[$tags]" "ResourceType=network-interface,Tags=[$tags]" \
    --query 'Instances[0].InstanceId' --output text)
  $aws $r ec2 wait instance-running --instance-ids "$id"
  ip=$($aws $r ec2 describe-instances --instance-ids "$id" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
  echo "launched $id at $ip; set HostName $ip under 'Host earthcraft-aws' in ~/.ssh/config, then: $0 push"
}

cmd=${1:-}; shift || true
case "$cmd" in
  bootstrap|run|update|launch|push) "$cmd" "$@" ;;
  status) status "$@" ;;
  *) if [ "$(basename "$0")" = earthcraft-status ]; then status "$cmd" "$@"; else sed -n 2,12p "$0"; exit 64; fi ;;
esac
