import duplicates


def test_detects_known_pair(tmp_path):
    for n, body in [("count-tokens", "x" * 500), ("count-file-tokens", "y" * 50)]:
        d = tmp_path / n
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {n}\ndescription: Count tokens in any file type.\n---\n{body}")
    pairs = duplicates.find_duplicate_pairs(tmp_path)
    assert len(pairs) == 1
    p = pairs[0]
    assert p.canonical == "count-tokens"      # larger body wins
    assert p.redundant == "count-file-tokens"


def test_distinct_skills_not_flagged(tmp_path):
    for n, desc in [("a", "Book a train ticket."), ("b", "Reconcile expenses.")]:
        d = tmp_path / n
        d.mkdir()
        (d / "SKILL.md").write_text(f"---\nname: {n}\ndescription: {desc}\n---\nbody")
    assert duplicates.find_duplicate_pairs(tmp_path) == []
