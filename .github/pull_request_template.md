## Summary

<!-- What does this PR change, and why? One or two sentences. -->

## Changes

<!-- Bullet the notable code changes. -->

-

## Integration / scope

<!-- Which sync(s) this touches: SanMar / S&S / Momentec / DC OneSource / shared.
     Note affected workflows or trigger files. -->

## Testing

- [ ] `pytest` passes
- [ ] `ruff check src tests` clean
- [ ] `mypy` clean
- [ ] Dry-run reviewed (if this changes what gets written to NetSuite)

<!-- Paste the relevant dry-run summary line(s). Do NOT include credentials,
     tokens, environment variables, or internal hostnames. -->

## Safety

- [ ] Sandbox-gated (`NETSUITE_ALLOW_PRODUCTION_WRITES=false` preserved)
- [ ] No secrets, `.env`, or credentials committed

## Related issues

<!-- e.g. Closes #123 -->
