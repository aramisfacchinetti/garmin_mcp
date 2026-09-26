"""Project metadata regression tests."""

from pathlib import Path


def test_project_caps_mcp_to_v1_series() -> None:
    """The project should explicitly stay on the MCP v1 series for compatibility."""
    repo_root = Path(__file__).resolve().parents[2]
    pyproject_text = (repo_root / "pyproject.toml").read_text()

    assert '"mcp>=1.28.1,<2"' in pyproject_text


def test_project_python_floor_matches_garminconnect_requirement() -> None:
    """The pinned Garmin client requires Python 3.12 or newer."""
    repo_root = Path(__file__).resolve().parents[2]
    pyproject_text = (repo_root / "pyproject.toml").read_text()

    assert 'requires-python = ">=3.12"' in pyproject_text
    assert '"garminconnect==0.3.6"' in pyproject_text
