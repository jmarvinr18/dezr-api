"""Files that run on Linux (in the containers, or on the EC2 instance through user data) must
have LF line endings. A CRLF shebang ("#!/bin/bash\r") fails with "required file not found":
that once kept a redeployed instance from ever starting the API."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINUX_FILES = sorted(
    {
        *ROOT.glob("*.sh"),
        *ROOT.glob("terraform/scripts/*.sh"),
        *ROOT.glob("terraform/templates/*.tftpl"),
        ROOT / "Dockerfile",
        ROOT / "docker-compose.yml",
    }
)


@pytest.mark.parametrize("path", LINUX_FILES, ids=[str(p.relative_to(ROOT)) for p in LINUX_FILES])
def test_linux_files_have_lf_line_endings(path):
    assert path.exists()
    assert b"\r\n" not in path.read_bytes(), f"{path.name} has CRLF line endings; convert it to LF"
