"""Acceptance worker entry for the isolated browser journey.

``uv run study-worker`` exits on the first notes, flashcards, or quiz job.
``ai_jobs.run`` imports ``studio`` before the API package is loaded:

``studio`` -> ``api.v1.materials`` -> ``api.v1`` -> ``chat`` -> ``resources``
then imports ``studio.count_due_flashcards`` while ``studio`` is still loading.

The API process imports the package first and does not hit this order.
Importing that package here lets the same product functions finish the
browser journey. This file does not change product code. The unwrapped
``uv run study-worker`` command remains the defect repro.
"""

import study_platform.api.v1  # noqa: F401
from study_platform.cli.worker import main

if __name__ == "__main__":
    main()
