---
name: earthcraft-audit
description: Audit Earthcraft dataset contributions by rendering their tiles and judging them with the local Claude Code CLI. Use when Eric says "audit", "check the contributions", "review the PRs", or runs /earthcraft-audit.
argument-hint: "[--pr N] [--latest] [--tiles X_Z,...] [--comment]"
---

Run the audit in the Earthcraft checkout:

```sh
cd ~/earthcraft && .venv/bin/python scripts/audit.py $ARGUMENTS
```

With no arguments it audits every open contribution pull request on the dataset: structural
validation, renders of four tiles per PR at `refs/pr/N`, and a visual verdict from `claude -p`
(the local CLI and your login, not the API). It needs `@playwright/test` (or `playwright`) and
`huggingface_hub`.

Then:

1. Read `runs/scratch/audit/<run>/report.md` and report each PR's verdict in one line, with the
   concrete defects of any FAIL.
2. Only post verdicts to Hugging Face when Eric asked for it (`--comment`). Never merge or close
   pull requests; Eric merges.
3. If a defect recurs across PRs, say so: it points at the generator, not the contributor.
4. Delete `runs/scratch/audit/<run>/` once Eric has seen the report, unless he asked to keep it.
