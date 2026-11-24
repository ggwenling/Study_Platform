"""Cold-process import of the worker's studio dependency.

The official worker loads ``study_platform.services.studio`` from a fresh
interpreter on the first flashcard job. A test that imports the API package
first hides the cycle, so this check uses a new interpreter.
"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def test_studio_imports_in_a_fresh_interpreter():
    script = (
        "import sys\n"
        "assert 'study_platform.api' not in sys.modules\n"
        "assert 'study_platform.services.studio' not in sys.modules\n"
        "from study_platform.services import studio\n"
        "from study_platform.api.v1 import resources\n"
        "assert callable(studio.count_due_flashcards)\n"
        "assert 'count_due_flashcards' not in resources.__dict__\n"
    )
    env = os.environ.copy()
    env.pop("PYTHONSTARTUP", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
