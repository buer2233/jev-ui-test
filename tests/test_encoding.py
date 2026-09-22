"""UTF-8 contract for text files reached outside of an import.

`tests/test_agent.py` imports `jev_ultrafast.browser`, whose module-level
`READ_STATE = Path(...).read_text(encoding="utf-8")` fails loudly on a
non-UTF-8 locale (cp936 on Chinese Windows). That import already guards
`snapshot.js`.

Text reached only at runtime is not guarded: `static/*.html` served by the
local inspector, and the JSON traces the scripts write. A file that fails to
decode there passes the whole suite and still breaks the app, which is exactly
how a UTF-8 asset shipped unnoticed.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Text is the default, so a new text file type is covered without an edit.
# Only suffixes that are known binaries in this repository are skipped.
BINARY_SUFFIXES = {
    ".gif", ".ico", ".jpeg", ".jpg", ".mp3", ".mp4", ".otf", ".pdf",
    ".png", ".ttf", ".wav", ".webm", ".webp", ".woff", ".woff2", ".zip",
}


def tracked_files():
    """Tracked paths, so `.gitignore` decides what ships instead of a skip list."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("Not a git checkout; there is no tracked-file inventory to check")
    return [name for name in listed.decode("utf-8", "surrogateescape").split("\0") if name]


def test_tracked_text_files_decode_as_utf8():
    failures = []
    for name in tracked_files():
        path = ROOT / name
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            failures.append(f"{name}: {error}")
        except OSError as error:
            failures.append(f"{name}: {error}")
    assert not failures, "Tracked text files are not valid UTF-8:\n" + "\n".join(failures)