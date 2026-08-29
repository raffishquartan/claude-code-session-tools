from cc_session_tools.lib.pdata.vector_clock import Comparison, bump_own, compare, merge


def test_compare_dominates_when_strictly_ahead_everywhere_or_equal():
    local = {"a": 1, "b": 2}
    dump = {"a": 1, "b": 3}
    assert compare(local=local, dump=dump) == Comparison.DUMP_DOMINATES


def test_compare_dominated_when_local_strictly_ahead():
    local = {"a": 1, "b": 3}
    dump = {"a": 1, "b": 2}
    assert compare(local=local, dump=dump) == Comparison.LOCAL_DOMINATES


def test_compare_equal_is_local_dominates_not_a_fork():
    # Equal vectors mean nothing to do - treated as LOCAL_DOMINATES (a no-op, not DUMP_DOMINATES
    # which would trigger a pointless rehydrate, and not FORK which would wrongly block).
    v = {"a": 1, "b": 2}
    assert compare(local=v, dump=dict(v)) == Comparison.LOCAL_DOMINATES


def test_compare_fork_when_each_side_has_something_the_other_lacks():
    local = {"a": 2, "b": 1}
    dump = {"a": 1, "b": 2}
    assert compare(local=local, dump=dump) == Comparison.FORK


def test_compare_handles_machine_known_to_only_one_side():
    # dump knows about "c", local never has - missing entries default to 0.
    local = {"a": 1}
    dump = {"a": 1, "c": 1}
    assert compare(local=local, dump=dump) == Comparison.DUMP_DOMINATES


def test_bump_own_increments_only_the_named_machine():
    v = {"ltxy": 3, "macbook": 5}
    bump_own(v, "ltxy")
    assert v == {"ltxy": 4, "macbook": 5}


def test_bump_own_creates_the_row_for_a_brand_new_machine():
    v: dict[str, int] = {}
    bump_own(v, "ltxy")
    assert v == {"ltxy": 1}


def test_merge_takes_elementwise_max():
    a = {"x": 1, "y": 5}
    b = {"x": 3, "y": 2, "z": 1}
    assert merge(a, b) == {"x": 3, "y": 5, "z": 1}
