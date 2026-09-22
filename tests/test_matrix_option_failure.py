"""One rejected option value must not cost a whole night's creates.

On 2026-09-22 the nightly SanMar leg died with a bare ``NetSuite 400: Bad
Request`` raised out of ``MatrixOptionResolver.resolve`` (run 35707265204).
Two things were wrong, and both are pinned here:

* the error named neither the list nor the value, so there was nothing to act
  on -- NetSuite's real reason sits in ``o:errorDetails``;
* it propagated out of ``main()``, abandoning all 1,332 net-new styles over a
  single size, while every other write in that same loop was guarded.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sanmar_netsuite.netsuite.client import NetSuiteError
from sanmar_netsuite.netsuite.matrix_options import (
    SIZE_LIST,
    MatrixOptionError,
    MatrixOptionResolver,
)


class _RejectingClient:
    """Finds nothing, and NetSuite rejects every create."""

    def suiteql(self, query: str, **kw):
        return []

    def create_record(self, record_type: str, body: dict) -> str:
        raise NetSuiteError(
            400, "Bad Request",
            {"o:errorDetails": [{"detail": "Invalid value for Name."}]},
        )


def test_a_rejected_create_names_the_list_the_value_and_netsuites_reason():
    resolver = MatrixOptionResolver(client=_RejectingClient(), allow_create=True)
    with pytest.raises(MatrixOptionError) as err:
        resolver.resolve(SIZE_LIST, "Toddler 2T/3T")
    text = str(err.value)
    assert SIZE_LIST in text                    # which list
    assert "Toddler 2T/3T" in text              # which value
    assert "Invalid value for Name." in text    # why, from o:errorDetails


def test_resolving_without_create_permission_still_reports_missing():
    # The guard must not turn a dry run's "missing" into a raise.
    resolver = MatrixOptionResolver(client=_RejectingClient(), allow_create=False)
    assert resolver.resolve(SIZE_LIST, "Toddler 2T/3T") == (None, "missing")


def test_one_rejected_size_raises_out_of_the_option_collection():
    # collect_new_options deliberately does NOT swallow: the caller decides
    # what a bad value costs. Here it must surface, named.
    import sanmar_parent_create as spc

    class _Sku:
        color_name = "Black"
        size = "2T/3T"

    class _Style:
        skus = [_Sku()]

    resolver = MatrixOptionResolver(client=_RejectingClient(), allow_create=True)
    with pytest.raises(MatrixOptionError):
        spc.collect_new_options(resolver, _Style())


def test_a_resolvable_style_reports_the_values_it_created():
    import sanmar_parent_create as spc

    class _OkResolver:
        def resolve(self, list_type, name):
            return "42", "created"

    class _Sku:
        color_name = "Black"
        size = "S"

    class _Style:
        skus = [_Sku()]

    assert spc.collect_new_options(_OkResolver(), _Style()) == {
        "colour 'Black'", "size 'Small'",
    }


def test_main_guards_the_call_so_one_style_cannot_kill_the_leg():
    # The guard itself lives in main()'s loop, which needs a live config to
    # run; assert its shape at the call site instead of not covering it.
    import sanmar_parent_create as spc

    src = Path(spc.__file__).read_text()
    guard = src.split("# Resolve (creating when live)")[1].split("done += 1")[0]
    assert guard.index("try:") < guard.index("collect_new_options(")
    assert guard.index("collect_new_options(") < guard.index("except Exception")
    assert guard.index("except Exception") < guard.index("failures += 1")
    assert guard.index("failures += 1") < guard.index("continue")
