# Security Policy

This repository holds proprietary integration code and connects to live vendor
and NetSuite systems. Treat it accordingly.

## Reporting a vulnerability

Do **not** open a public issue for a security problem. Report it privately to
**andy@bsgsports.com** with:

- a description of the issue and its potential impact, and
- steps to reproduce (without including live credentials or secrets).

You'll get an acknowledgement, and we'll coordinate a fix and disclosure
timeline privately.

## Handling secrets

- Never commit credentials, tokens, `.env` files, SFTP passwords, or NetSuite
  TBA keys. They live in GitHub Actions repository secrets and are injected at
  runtime only.
- If a secret is committed or otherwise exposed, **rotate it immediately** at
  the source (SanMar, S&S, NetSuite, etc.), then purge it from history.
- Do not paste secrets, internal hostnames, or account numbers into issues,
  pull requests, or logs.

## Write safety

All NetSuite-touching workflows run against the **sandbox** with
`NETSUITE_ALLOW_PRODUCTION_WRITES=false`, and most default to `SYNC_DRY_RUN=true`.
These guards must stay in place unless a production change is explicitly
reviewed and approved. When in doubt, dry-run first and inspect the reported
impact before any live write.

## Supported versions

Only the current default branch is maintained. Fixes land there and roll
forward; older branches are not separately patched.
