import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# In a clean interpreter, since another test having imported tinytuya.scanner
# would hide the bug: an attribute of the package appears on the first import.
FROZEN_RUN = """
import builtins, sys
sys.path.insert(0, sys.argv[1])
sys.frozen = True
builtins.input = lambda *_: ""
import tinytuya, tinytuya.wizard

def fake_wizard():
    print("scanner available:", hasattr(tinytuya, "scanner"))

tinytuya.wizard.wizard = fake_wizard
import setup_wizard
setup_wizard.main()
"""


def test_packaged_wizard_can_reach_the_scanner(tmp_path):
    """tinytuya's wizard calls tinytuya.scanner without importing it (its
    `python -m tinytuya` entry does that). The exe runs the wizard in-process,
    so a Device ID of `scan` failed with "module 'tinytuya' has no attribute
    'scanner'"."""
    result = subprocess.run(
        [sys.executable, "-c", FROZEN_RUN, str(REPO / "tools")],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "scanner available: True" in result.stdout
