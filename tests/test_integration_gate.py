import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("configured", [False, True])
def test_live_gate_cannot_pass_without_executing_live_tests(configured):
    env = os.environ | {
        "GOOGLE_SA_JSON": "synthetic" if configured else "",
        "IAA_DEV_SHEET_ID": "synthetic" if configured else "",
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_policy.py", "--require-live", "-q"],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    expected = "live validation incomplete" if configured else "live validation requires"
    assert expected in result.stdout + result.stderr
