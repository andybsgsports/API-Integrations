"""Probe: SOAP getAll for itemCustomField -- what shape does the response
take? REST 404s on this record type entirely, so we need SOAP's getAll to
resolve scriptId -> internalId for every custom item field before we can
update their descriptions (SOAP update needs internalId, not scriptId).
Prints the raw response so the parser can be built against real evidence.
"""

from __future__ import annotations

from sanmar_netsuite.config import get_config
from ns_soap import post


def main() -> int:
    cfg = get_config().netsuite
    body = """
    <platformMsgs:getAll>
      <platformMsgs:record recordType="itemCustomField"/>
    </platformMsgs:getAll>"""
    text = post(cfg, "getAll", body)
    print(f"response length: {len(text)}")
    print(text[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
