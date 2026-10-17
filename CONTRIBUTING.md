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

1. **Get the code and tools.** Install Python 3.11 or newer and git. Then run
   `git clone https://github.com/EricSpencer00/earthcraft && cd earthcraft`,
   `python3 -m venv .venv` and `.venv/bin/pip install -r requirements-contribute.txt`.
   Keep about 30 GB of disk free.
2. **Have Minecraft 1.21.10.** Open the official Minecraft launcher, pick
   version 1.21.10 and start it once. That puts the game file the tool reads
   block colours from on your computer. The tool never uploads it. If it is
   somewhere unusual, pass `--client-jar /path/to/1.21.10.jar`.
3. **Log in to Hugging Face.** Make a free account, create an access token with
   write access at huggingface.co/settings/tokens, and run
   `.venv/bin/hf auth login` (or set `HF_TOKEN`).
4. **Run it.** `.venv/bin/python scripts/contribute.py --tiles 20`. It picks 20
   tiles that nobody has made or claimed yet, builds them (a few minutes each),
   and opens one pull request on the dataset. Add `--dry-run` to only see which
   tiles it would pick. Several computers can split the work with
   `--shard 0/3`, `--shard 1/3`, `--shard 2/3`.
5. **Wait for the check.** Within about half an hour a validator comments
   **PASS** or **FAIL** on your pull request, with the reason for any failure.
   The owner reviews passing requests and merges them.

Everything the tool downloads is public: the plan and templates come from the
dataset (the templates hold no player data), map sources from public servers.

### Merging a passing pull request (owner)

Open the pull request on the dataset's Community tab, read the validator's
comment, and look at the files: only `tiles/v2/<tile>/...` and one
`contributions/<name>.json`. If it says PASS, press **Merge**. The bot never
merges. After the merge the AWS hosts adopt those tiles instead of making them
again. Close failing or stale requests with a short note.
