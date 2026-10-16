import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Emoji, pictographs, dingbats (check marks), misc technical symbols (hourglass), etc.
EMOJI = re.compile("[\U0001f000-\U0001ffff\u2300-\u23ff\u2600-\u27bf\u2b00-\u2bff\ufe0f]")
SKIP_SUFFIXES = (".lock", "lock.yaml", ".ico", ".png", ".jpg", ".pdf", ".svg", ".woff", ".woff2")


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO_ROOT / f for f in out.splitlines() if not f.endswith(SKIP_SUFFIXES)]


def test_repository_contains_no_emojis() -> None:
    offenders = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if EMOJI.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert offenders == [], f"emojis found (project rule: none anywhere): {offenders}"
