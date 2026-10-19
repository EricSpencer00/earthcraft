# AWS pipeline host

One small always-on EC2 spot instance runs the regional generator
([Chicagoland scans](CHICAGOLAND_SCANS.md)) continuously and commits each batch of
tiles straight to the public dataset under `tiles/v2/<tile>/`
([the dataset](DATASET.md)). It is the pipeline's only compute host; the
MacBook coordinates it with the AWS CLI and ssh only.

## Host

| Item | Value |
| --- | --- |
| Region / AZ | us-west-2 (next to the public USGS LiDAR bucket), us-west-2d (cheapest m6a spot) |
| Instance | m6a.large spot (2 vCPU, 8 GB), persistent request, interruption behaviour `stop` |
| Disk | 60 GB gp3 root, deleted with the instance |
| Image | Ubuntu 24.04 LTS x86_64 |
| Access | security group `earthcraft-pipeline`: SSH from the coordinator's IP only; key pair `earthcraft-pipeline` |
| Secrets | HF write token in SSM SecureString `/earthcraft/hf_token`; the instance role `earthcraft-pipeline` may only `ssm:GetParameter` it (and decrypt with the default `aws/ssm` key). It is read into the service's environment at start and never written to disk. |
| Tags | `Project=earthcraft`, `Owner=EricSpencer00`, `Name=earthcraft-pipeline` |
| Budget | `earthcraft-monthly`, $35, e-mail at 80% actual and 100% forecast |

Approximate monthly cost: spot about $0.034/h x 730 h = $25, 60 GB gp3 = $4.80,
public IPv4 $3.65, egress to the Hub a few dollars at most (packages are a
few MB per tile; data transfer out is $0.09/GB after the first 100 GB/month,
which the account's free tier covers at the measured rate). Source reads from
the USGS bucket in the same region are free.

## Layout on the instance

```
/opt/earthcraft                 read-only HTTPS checkout of this repository (branch from /etc/earthcraft/pipeline.env)
/opt/earthcraft/.venv           Python 3.12: requirements-chicago, -lidar, -footprints, huggingface_hub 1.8, hf_xet, boto3
/opt/earthcraft/vendor/minecraft-client-1.21.10.jar   private (block colours), copied by `push`
/opt/earthcraft/runs/regional/control/                plan, journal, catalogs, templates (private), logs
/opt/earthcraft/runs/chicago-adaptation-city-001/frame.json
/opt/earthcraft/runs/scratch/{cache,gen}              guarded: cache cap 20 GiB, footprint cache + 5 GiB per worker + 1 GiB, free floor 10 GiB
/opt/earthcraft/runs/status/pipeline.json             host status, refreshed every 10 min
```

No Java is installed: the regional generator verifies worlds in Python.

## Units

* `earthcraft-pipeline.service` runs `aws_pipeline_setup.sh run`: reads the token
  from SSM, then `regional_supervisor.py --footprints --workers $EARTHCRAFT_WORKERS --batch-tiles 8`
  at nice 10, idle-ish I/O priority, `Restart=always`, enabled at boot, so it
  resumes by itself after a reboot or a spot stop/start (the worker requeues its
  own leases; a tile already on the Hub is adopted). Logs: journald for the
  supervisor, `control/logs/base-1.log` for the worker (JSON lines).
  `KillMode=mixed` sends SIGTERM to the supervisor only; the worker then finishes
  its tile and commits its batch before exiting (`EARTHCRAFT_STOP_TIMEOUT=1800`).
* `earthcraft-update.timer` (every 30 min) runs `update`: `git fetch` over HTTPS
  (no credentials on the box); if the branch moved it fast-forwards, reinstalls
  requirements when a `requirements*.txt` changed, reinstalls units when
  `configs/aws/` or this script changed, and restarts the pipeline gracefully as
  above when worker scripts, requirements, AWS configuration or the shard changed.
  Frontend/documentation-only updates leave generation running. A missing branch
  falls back to `main`.
* `earthcraft-status.timer` (every 10 min) writes `runs/status/pipeline.json`
  (`earthcraft.pipeline-status.v1`: tiles last hour / 24 h, tiles per hour, last
  commit, journal counts, disk, git SHA, spot state). The generator adds it to
  the next tile commit as `status/pipeline.json` at most once an hour
  (`EARTHCRAFT_STATUS_FILE`), so it costs no extra commits.

## Operate (from the MacBook)

```sh
ssh earthcraft-aws earthcraft-status                       # tiles, last commit, disk, spot state
ssh earthcraft-aws journalctl -u earthcraft-pipeline -f    # supervisor
ssh earthcraft-aws tail -f /opt/earthcraft/runs/regional/control/logs/base-1.log
ssh earthcraft-aws sudo systemctl stop earthcraft-pipeline  # graceful: finishes the tile and batch
ssh earthcraft-aws sudo systemctl start earthcraft-pipeline
AWS=~/.local/bin/aws; I=$($AWS --region us-west-2 ec2 describe-instances --filters Name=tag:Name,Values=earthcraft-pipeline \
  Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text)
$AWS --region us-west-2 ec2 stop-instances --instance-ids $I    # pause billing for compute (EBS still billed)
$AWS --region us-west-2 ec2 start-instances --instance-ids $I   # the service starts itself; the public IP changes:
$AWS --region us-west-2 ec2 describe-instances --instance-ids $I --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
```

After a start, put the new address in `HostName` under `Host earthcraft-aws` in
`~/.ssh/config`. If the coordinator's public IP changes, replace the security
group's SSH rule.

## Rebuild from scratch

```sh
scripts/aws_pipeline_setup.sh launch   # spot instance with configs/aws/cloud-init.yaml (public bootstrap)
scripts/aws_pipeline_setup.sh push     # client JAR, templates, frame -> the instance; starts the service
```

`launch` reuses the key pair, security group and instance profile.
`launch --count N` adds N identical boxes and re-tags every box
`EarthcraftShard=i/T`; each box claims only its share of the plan (see
[Scaling](PIPELINE.md#scaling)). `--dry-run` prints the calls only.
Cloud-init downloads this script from `main` and runs `bootstrap` (packages,
checkout, venv, units, timers). The service waits (`ConditionPathExists`) for the
private inputs, which never go into user data or the public repository. A
fresh control directory is seeded from `archive/mini-worker-20260929/`
(`--seed-control`); tiles already on the Hub are adopted, not regenerated.

The supervisor keeps its 45-day deadline in `control/supervisor-deadline.json`;
remove that file to extend the run.

## Tear down

Cancel the spot request first (otherwise it relaunches), then terminate:

```sh
$AWS --region us-west-2 ec2 cancel-spot-instance-requests --spot-instance-request-ids <sir-id>
$AWS --region us-west-2 ec2 terminate-instances --instance-ids $I
$AWS --region us-west-2 ec2 delete-security-group --group-name earthcraft-pipeline   # after termination
$AWS --region us-west-2 ec2 delete-key-pair --key-name earthcraft-pipeline
$AWS --region us-west-2 ssm delete-parameter --name /earthcraft/hf_token
$AWS iam remove-role-from-instance-profile --instance-profile-name earthcraft-pipeline --role-name earthcraft-pipeline
$AWS iam delete-instance-profile --instance-profile-name earthcraft-pipeline
$AWS iam delete-role-policy --role-name earthcraft-pipeline --policy-name earthcraft-hf-token
$AWS iam delete-role --role-name earthcraft-pipeline
$AWS budgets delete-budget --account-id 971966099666 --budget-name earthcraft-monthly
```
