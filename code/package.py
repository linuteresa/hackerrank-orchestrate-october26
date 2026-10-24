"""Build ``code.zip`` for submission (run from anywhere; writes ``<repo>/code.zip``).

Contents: ``README.md`` and the ``code/`` tree. Excluded: Python caches, the model-output cache
(``code/cache/``), scratch run outputs in ``code/eval/`` (provisional predictions, reports, export
classes), nested archives, environment files and virtual environments. The archive is deterministic
(sorted entries, fixed timestamps). Before writing, every included file is scanned for
credential-like content and for the value of ``ANTHROPIC_API_KEY`` if it is set; on a match the
offending file names (never the content) are printed and nothing is written.

    python3 code/package.py
"""

import hashlib
import os
import re
import sys
import zipfile
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
REPO = CODE_DIR.parent
TARGET = REPO / "code.zip"

REQUIRED = ("README.md", "code/main.py", "code/requirements.txt",
            "code/evaluation/evaluation_report.md", "code/evaluation/usage_report.md")
EXCLUDED_DIRS = {"__pycache__", "cache", ".venv", "venv", "node_modules", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".env"}
EXCLUDED_NAMES = {".DS_Store", ".env"}
SCRATCH = re.compile(r"^code/eval/(provisional_|unresolved_|usage_|export_classes_|sample_predictions).*")
# Values, not mentions: a key-shaped token, or a credential header followed by a long token.
CREDENTIAL = re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}"
                        rb"|(?i:x-api-key|authorization)[\"']?\s*[:=]\s*[\"']?(?:Bearer\s+)?[A-Za-z0-9_.\-]{16,}")
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def selected_files():
    files = [REPO / "README.md"]
    for path in sorted(CODE_DIR.rglob("*")):
        rel = path.relative_to(REPO).as_posix()
        if not path.is_file() or EXCLUDED_DIRS & set(path.relative_to(REPO).parts[:-1]):
            continue
        if path.suffix in EXCLUDED_SUFFIXES or path.name in EXCLUDED_NAMES or SCRATCH.match(rel):
            continue
        files.append(path)
    return files


def main():
    files = selected_files()
    names = [p.relative_to(REPO).as_posix() for p in files]
    missing = [r for r in REQUIRED if r not in names]
    if missing:
        print("package refused: missing required files:", missing)
        return 1
    secret = os.environ.get("ANTHROPIC_API_KEY", "").encode()
    flagged = [n for p, n in zip(files, names)
               if CREDENTIAL.search(p.read_bytes()) or (secret and secret in p.read_bytes())]
    if flagged:
        print("package refused: credential-like content in:", flagged)
        return 1
    with zipfile.ZipFile(TARGET, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in zip(files, names):
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    digest = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    print(f"code.zip written: {len(names)} files, {TARGET.stat().st_size} bytes, sha256 {digest}")
    for name in names:
        print("  ", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
