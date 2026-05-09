from cc_session_tools.lib.levenshtein import distance


def test_distance_identical_strings_is_zero():
    assert distance("oneshot", "oneshot") == 0


def test_distance_one_substitution():
    assert distance("oneshot", "oneshet") == 1


def test_distance_two_substitutions():
    assert distance("oneshot", "oneshes") == 2


def test_distance_one_insertion():
    assert distance("oneshot", "oneshott") == 1


def test_distance_one_deletion():
    assert distance("oneshot", "neshot") == 1


def test_distance_empty_to_word():
    assert distance("", "oneshot") == 7


def test_distance_word_to_empty():
    assert distance("oneshot", "") == 7


def test_distance_completely_different():
    assert distance("foo", "bar") == 3


def test_distance_is_symmetric():
    assert distance("oneshet", "oneshot") == distance("oneshot", "oneshet")
