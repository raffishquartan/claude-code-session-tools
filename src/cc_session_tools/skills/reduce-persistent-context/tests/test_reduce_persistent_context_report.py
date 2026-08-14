from measure import Contributor
from duplicates import DuplicatePair
import report


def test_unused_high_cost_is_strong():
    recs = [Contributor("opentabs", "mcp_names", 9000)]
    ranked = report.rank(recs, {"opentabs": 0}, dup_pairs=[])
    assert ranked[0]["tier"] == "strong"


def test_used_high_cost_is_trim():
    recs = [Contributor("opentabs", "mcp_names", 9000)]
    ranked = report.rank(recs, {"opentabs": 500}, dup_pairs=[])
    assert ranked[0]["tier"] == "trim"


def test_redundant_dup_is_strong():
    recs = [Contributor("count-file-tokens", "skill_desc", 80)]
    pairs = [DuplicatePair("count-tokens", "count-file-tokens", "known pair")]
    ranked = report.rank(recs, {}, dup_pairs=pairs)
    row = next(r for r in ranked if r["name"] == "count-file-tokens")
    assert row["tier"] == "strong"


def test_render_md_has_total():
    md = report.render_markdown(
        [{"name": "x", "category": "harness", "tokens": 10,
          "tier": "keep", "usage": None}], total=10)
    assert "10" in md and "Total" in md
