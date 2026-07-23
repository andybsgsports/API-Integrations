# Contributing

Internal guide for developing on Badger Sporting Goods' supplier→NetSuite
integrations. This is a private, proprietary repository (see [`LICENSE`](LICENSE));
access is limited to authorized BSG personnel and contractors.

## Development setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in your own sandbox creds — never commit .env
```

Requires Python 3.10+.

## Before you push

Run the same checks CI runs:

```bash
pytest                    # full unit suite (should be green; 1 skip is expected)
ruff check src tests      # lint
mypy                      # type check
```

Keep new code covered by tests. Match the surrounding style — small, focused
functions with a one-line docstring, and comments that explain *why*, not *what*.

## Branching & commits

- Branch off the current default branch; use a descriptive branch name.
- Never push directly to the default branch — open a pull request.
- Write imperative, self-contained commit messages that say what changed and
  why (e.g. "Cost basis = case price, not single-piece price").

## The trigger-file workflow pattern

Most operational scripts run as GitHub Actions driven by a **trigger file** at
the repo root (e.g. `.sanmar-update-trigger`, `.ss-backfill-trigger`). Each is a
`KEY=VALUE` file; editing it and pushing fires the matching workflow, which
parses the keys (dry-run flags, caps, ids). To iterate:

1. Edit the `.*-trigger` file (bump its `# run:` marker so the change is real).
2. Commit and push — the workflow runs on the push.
3. Read the run logs; flip `DRY_RUN=false` only once the dry run looks right.

`data/` is gitignored; if a workflow needs a generated file committed, add it
with `git add -f`.

## Safety: sandbox-first, always

Every workflow sets `NETSUITE_ALLOW_PRODUCTION_WRITES=false` and most default to
`SYNC_DRY_RUN=true`. **Do not** remove or flip these gates without an explicit,
reviewed reason. Prefer a dry run that reports the impact before any live write.

## Secrets

Never commit credentials, tokens, `.env` files, SFTP passwords, or NetSuite TBA
keys. Secrets live in GitHub Actions repository secrets and are injected at
runtime. If you think a secret has leaked, rotate it and see
[`SECURITY.md`](SECURITY.md).

## Pull requests

Open PRs as drafts, fill in the [PR template](.github/pull_request_template.md),
and make sure `pytest` / `ruff` / `mypy` pass. Describe the code change and how
you verified it — do not paste credentials, tokens, or internal hostnames.
