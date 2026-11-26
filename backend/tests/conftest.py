import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@pytest.fixture
def make_symlink():
    def create(link, target, *, target_is_directory=False):
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as exc:
            if os.name == "nt" and exc.winerror in (1, 50, 1314):
                pytest.skip(f"Windows cannot create symbolic links here (WinError {exc.winerror})")
            raise

    return create
