from cc_session_tools.lib.tasklist import id_for_project


def test_returns_basename_when_parent_is_a_root(tmp_path):
    root = tmp_path / "myroot"
    root.mkdir()
    project = root / "myproject"
    project.mkdir()
    assert id_for_project(project, roots=[root]) == "myproject"


def test_returns_none_when_not_a_direct_child_of_any_root(tmp_path):
    root = tmp_path / "myroot"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert id_for_project(elsewhere, roots=[root]) is None


def test_returns_none_when_two_levels_deep_under_a_root(tmp_path):
    root = tmp_path / "myroot"
    root.mkdir()
    middle = root / "middle"
    middle.mkdir()
    project = middle / "project"
    project.mkdir()
    assert id_for_project(project, roots=[root]) is None


def test_resolves_symlinks_for_project_dir(tmp_path):
    root = tmp_path / "myroot"
    root.mkdir()
    project = root / "myproject"
    project.mkdir()
    link = tmp_path / "link-to-project"
    link.symlink_to(project)
    assert id_for_project(link, roots=[root]) == "myproject"
