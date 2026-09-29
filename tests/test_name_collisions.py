from sanmar_netsuite.netsuite.collisions import find_name_collisions, split_payloads


class FakeClient:
    """SuiteQL-only stand-in: never writes."""

    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def suiteql(self, q, **_):
        self.queries.append(q)
        return self.rows


def test_matches_parent_prefixed_itemid_case_insensitive():
    c = FakeClient([{"id": "9", "itemid": "PC90 : PC90-White-Small", "isinactive": "F"}])
    hits = find_name_collisions(c, ["PC90-White-Small", "PC90-Navy-Small"])
    assert list(hits) == ["PC90-WHITE-SMALL"]
    assert hits["PC90-WHITE-SMALL"]["id"] == "9"


def test_suffix_lookalike_is_not_a_collision():
    # '%PC90-White-Small' also matches 'XPC90-White-Small'; must not count.
    c = FakeClient([{"id": "1", "itemid": "XPC90 : XPC90-White-Small", "isinactive": "F"}])
    assert find_name_collisions(c, ["PC90-White-Small"]) == {}


def test_inactive_flag_and_split():
    c = FakeClient([{"id": "5", "itemid": "29M : 29M-J.Navy-Large", "isinactive": "T"}])
    hits = find_name_collisions(c, ["29M-J.Navy-Large"])
    assert hits["29M-J.NAVY-LARGE"]["inactive"] is True
    post, skipped = split_payloads(
        [{"itemId": "29M-J.Navy-Large"}, {"itemId": "29M-Black-Large"}], hits
    )
    assert [p["itemId"] for p in post] == ["29M-Black-Large"]
    assert skipped[0][1]["id"] == "5"


def test_only_reads():
    c = FakeClient([])
    find_name_collisions(c, ["A-B-C"] * 3)
    assert all(q.lstrip().upper().startswith("SELECT") for q in c.queries)
