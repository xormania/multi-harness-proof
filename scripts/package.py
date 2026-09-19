#!/usr/bin/env python3
"""Package source only; never include run directories or diagnostic archives."""
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = ("proof.py", "README.md", "AGENTS.md", "CLAUDE.md", "LICENSE", ".gitignore", "VALIDATION.md")


def source_paths():
    paths = [ROOT / name for name in TOP]
    for directory in ("mhproof", "tests", "scripts", "fixtures"):
        paths.extend(p for p in (ROOT / directory).iterdir()
                     if p.is_file() and not p.is_symlink() and p.suffix in (".py", ".sh", ".md", ".json"))
    return sorted(paths)


def main():
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "multi-harness-proof.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in source_paths():
            relative = path.relative_to(ROOT)
            info = zipfile.ZipInfo("multi-harness-proof/" + relative.as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if path.suffix == ".sh" or path.name == "proof.py" else 0o100644) << 16
            archive.writestr(info, path.read_bytes())
    print(destination.resolve())


if __name__ == "__main__":
    main()
