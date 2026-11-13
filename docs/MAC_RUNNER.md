# Mac runner

Tiles are built on **mb16**, Eric's 16-inch Intel MacBook Pro (12 threads, 16 GB), at no cost.
The same script that runs the Linux hosts runs it; on macOS everything lives in the checkout and
the user's own LaunchAgents, with no root.

| | |
| --- | --- |
| Checkout | `~/earthcraft` |
| Settings | `~/.config/earthcraft/pipeline.env` (`EARTHCRAFT_WORKERS`, `EARTHCRAFT_LANE=quality`, `EARTHCRAFT_SHARD`) |
| HF write token | `~/.config/earthcraft/hf_token`, mode 600 |
| Agents | `com.earthcraft.pipeline` (supervisor, kept alive), `com.earthcraft.pipeline.status` (every 15 min) |
| Logs | `runs/regional/logs/`, worker logs in `runs/regional/control/logs/` |

## Start

```sh
git clone https://github.com/EricSpencer00/earthcraft ~/earthcraft && cd ~/earthcraft
install -m 600 /dev/stdin ~/.config/earthcraft/hf_token      # paste the token, Ctrl-D (mkdir -p the folder first)
scripts/aws_pipeline_setup.sh mac-install
```

`mac-install` makes the venv (Python 3.11+), installs packages, fetches the public inputs
(plan and player-free templates from the dataset, the client JAR from Mojang), and loads both
agents. Run it again after `git pull` to pick up new code.

## Check and stop

```sh
scripts/aws_pipeline_setup.sh status
scripts/aws_pipeline_setup.sh mac-stop
```

`mac-stop` lets each worker finish its tile and commit its batch, then publishes a status that
says `inactive`. The health monitor reads that as a deliberate stop. A Mac that is switched off
or crashes leaves its last `active` report to go stale, and the monitor opens an issue.

The supervisor holds a `caffeinate -i` assertion, so the Mac does not idle-sleep while it runs.
Closing the lid on battery still sleeps it; workers resume their tiles on wake.

## With a second runner

Give each a share so they never build the same tile: `EARTHCRAFT_SHARD=0/2` here and `1/2` on the
other host ([ORACLE_PIPELINE.md](ORACLE_PIPELINE.md)). A tile already on the dataset is adopted,
not rebuilt.
