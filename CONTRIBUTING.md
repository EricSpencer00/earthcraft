# Contributing

Read [README.md](README.md) and [AGENTS.md](AGENTS.md) first.

- Keep one block = one metre, and keep source and licence with every derived layer.
- Open an issue before changing the data layout, the coordinate frame, or source policy.
- Use public landmarks or synthetic fixtures in examples. No private addresses or photos.
- Do not commit worlds, raw downloads, logs, credentials, or machine paths.
- Do not add `Co-authored-by` trailers; CI rejects them.

Before a pull request:

```sh
PYTHONPATH=scripts .venv/bin/python -m unittest discover -s tests
python3 scripts/public_tree_check.py
```

Say what changed, why, and how you tested it.

## Contribute compute

You can generate map tiles on your own computer and offer them to the dataset.
Your computer does the work; the owner checks it and adds it.

The short way is one command, on macOS or Linux with git and 20 GB of free disk:

```sh
curl -fsSL https://ericspencer.us/earthcraft/contribute.sh | sh
```

It does steps 1 to 4 below in `~/.earthcraft` (set `EARTHCRAFT_DIR` to move it) and passes
any options on: `... | sh -s -- --tiles 20`. The steps by hand:

1. **Get the code and tools.** Install Python 3.11 or newer and git. Then run
   `git clone https://github.com/EricSpencer00/earthcraft && cd earthcraft`,
   `python3 -m venv .venv` and `.venv/bin/pip install -r requirements-contribute.txt`.
   Keep about 30 GB of disk free.
2. **Minecraft 1.21.10's game file.** The tool reads block colours from the
   official client JAR. It uses the one in your Minecraft launcher if you have
   started 1.21.10 once; otherwise it downloads the file from Mojang's own
   server and checks its hash. The tool never uploads it. If it is somewhere
   unusual, pass `--client-jar /path/to/1.21.10.jar`.
3. **Log in to Hugging Face.** Make a free account, create an access token with
   write access at huggingface.co/settings/tokens, and run
   `.venv/bin/hf auth login` (or set `HF_TOKEN`).
4. **Run it.** `.venv/bin/python scripts/contribute.py`. It starts one worker
   per spare core (half your CPUs, at most 8, fewer if disk is short; `--cores N`
   to choose) and builds the unclaimed tiles nearest the finished area first.
   Finished tiles are uploaded in batches of six per worker, as commits on your
   one pull request; the pull request's manifest is updated with it. Press Ctrl-C to
   stop: workers finish the tile in hand. The next run adds to the same pull
   request until it holds 200 tiles, then opens a new one. `--tiles 20` stops
   after twenty, `--dry-run` only shows the picks, and `--shard i/N` splits the
   work between several computers. Dataset owners and writers can pass `--direct`
   to commit to `main` as the runners do, with no pull request.
5. **Wait for the check.** Within about half an hour a validator comments
   **PASS** or **FAIL** on your pull request, with the reason for any failure.
   The owner audits the renders (`scripts/audit.py`) and merges passing requests.

Everything the tool downloads is public: the plan and templates come from the
dataset (the templates hold no player data), map sources from public servers.

### Merging a passing pull request (owner)

Run `scripts/audit.py` (or `/earthcraft-audit` in Claude Code): it validates every
open pull request, renders four of its tiles and has the local Claude Code CLI judge
them, and writes a report. Then open the pull request on the dataset's Community tab,
read the validator's comment, and look at the files: only `tiles/v2/<tile>/...` and one
`contributions/<name>.json`. If it says PASS, press **Merge**. The bot never
merges. After the merge the runners adopt those tiles instead of making them
again. Close failing or stale requests with a short note.
