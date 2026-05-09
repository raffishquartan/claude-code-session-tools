from __future__ import annotations

import pytest

from cc_session_tools import __version__
from cc_session_tools.cli import ccd, ccr, ccs


@pytest.mark.parametrize("module", [ccd, ccr, ccs])
def test_version_flag_prints_package_version_and_exits_zero(module, capsys):
    with pytest.raises(SystemExit) as exc_info:
        module.main(["--version"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    # argparse prints "<prog> <version>"
    assert __version__ in out
