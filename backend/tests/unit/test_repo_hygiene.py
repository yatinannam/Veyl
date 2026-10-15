from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INIT_DIR = REPO_ROOT / "docker" / "postgres" / "init"


def test_postgres_init_dir_has_scripts() -> None:
    assert any(INIT_DIR.glob("*.sh"))


def test_postgres_init_scripts_use_lf_line_endings() -> None:
    # CRLF in a shell script breaks Postgres bootstrap silently on Windows checkouts.
    for script in INIT_DIR.iterdir():
        assert b"\r" not in script.read_bytes(), f"{script.name} has CRLF line endings"
