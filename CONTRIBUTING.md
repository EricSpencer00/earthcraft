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
