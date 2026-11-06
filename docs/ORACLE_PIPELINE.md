# Oracle Cloud pipeline host

The regional generator also runs on Oracle Cloud's Always Free tier, at no cost:
one Ampere A1 instance with 4 OCPU, 24 GB of memory and a boot volume up to 200 GB.
It uses the same script, units and timers as the [AWS host](AWS_PIPELINE.md).
Only the secrets path differs. There is no SSM or S3, so `push` copies the HF write
token as well as the private inputs.

| | AWS (`m5a.large` spot) | Oracle (`VM.Standard.A1.Flex`) |
| --- | --- | --- |
| Cost | within the $35/month cap | $0 (Always Free) |
| CPU, memory | 2 vCPU, 8 GB | 4 OCPU (aarch64), 24 GB |
| Workers, `MemoryMax` | 2, 7G | 4, 20G (`EARTHCRAFT_MEMORY_MAX`) |
| HF token | SSM `/earthcraft/hf_token` | `/etc/earthcraft/hf_token`, root:ubuntu 0640 |
| Private inputs | S3, by the instance role | `push` from the coordinator |

## Create the instance (Eric, in the Oracle console)

1. Sign up at [oracle.com/cloud/free](https://www.oracle.com/cloud/free/).
   Pick a home region you can keep; Always Free A1 capacity lives only there.
2. **Compute → Instances → Create instance**:
   - Image: **Canonical Ubuntu 24.04** (aarch64).
   - Shape: **Ampere → VM.Standard.A1.Flex**, 4 OCPU, 24 GB.
   - Networking: the default VCN with a public IPv4 address.
   - SSH keys: paste `~/.ssh/id_ed25519.pub` (or another key the MacBook holds).
   - Boot volume: **100 GB** (cache 20 GiB + 5 GiB per worker + 1 GiB, a 10 GiB floor, and the OS).
   - Advanced → Management → cloud-init script: upload `configs/oci/cloud-init.yaml`.
3. "Out of capacity for shape VM.Standard.A1.Flex" is common: retry later or pick another
   availability domain. Upgrading the account to Pay As You Go usually clears it, and
   Always Free shapes stay free.

## Start it (coordinator)

```sh
# ~/.ssh/config: Host earthcraft-oci / HostName <public ip> / User ubuntu
ssh earthcraft-oci 'sudo cloud-init status --wait; tail -3 /var/log/earthcraft-bootstrap.log'
scripts/aws_pipeline_setup.sh push earthcraft-oci     # JAR, templates, frame, HF token; starts the service
ssh earthcraft-oci earthcraft-status
```

`push` reads the token from `~/.cache/huggingface/token` (override with `HF_TOKEN_FILE`).
The bootstrap builds `rasterio 1.4.3` from source against the system GDAL, because that
pin has no aarch64 wheel. This adds a few minutes to the first boot.

## Running beside AWS

Each host keeps its own job journal. While the AWS host is stopped, the Oracle host takes
the whole plan. If both run, give each a share so they never build the same tile: tag the
AWS instance `EarthcraftShard=0/2` and add `EARTHCRAFT_SHARD=1/2` to the Oracle host's
`/etc/earthcraft/pipeline.env`. The update timer restarts each service after its current
tile and batch.

Oracle reclaims Always Free instances that stay idle (CPU under 20%) for 7 days. A running
generator stays well above that.
