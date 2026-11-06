"""Bootstrap the frozen CLI with its bundled grammars and project test tools."""

import os
import shutil
import sys
from pathlib import Path

from ub_cli.cli import main


def project_interpreter(directory: Path) -> str:
    """Use the project's interpreter, then Python on PATH for target tests."""
    for name in (".venv", "venv"):
        candidate = directory / name / "bin" / "python"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.absolute())
    executable = shutil.which("python3")
    if executable is None:
        raise ValueError(
            "Python tests require a project .venv/bin/python, python3 on PATH, "
            "or an explicit --test-command."
        )
    return executable


if getattr(sys, "frozen", False):
    if sys.argv[1:] == ["--licenses"]:
        bundled = Path(sys._MEIPASS)
        for notice in [
            bundled / "LICENSE",
            bundled / "THIRD_PARTY_NOTICES.md",
            *sorted((bundled / "licenses").rglob("*")),
        ]:
            if notice.is_file():
                print(f"\n{notice.relative_to(bundled)}\n")
                print(notice.read_text(encoding="utf-8", errors="replace"))
        raise SystemExit(0)

    import mutator.runner
    import tree_sitter_language_pack as languages

    languages.configure(
        languages.PackConfig(cache_dir=str(Path(sys._MEIPASS) / "grammar-cache"))
    )
    mutator.runner._project_interpreter = project_interpreter
    # External compilers and project interpreters must load their own libraries.
    if sys.platform.startswith("linux"):
        original = os.environ.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            os.environ.pop("LD_LIBRARY_PATH", None)
        else:
            os.environ["LD_LIBRARY_PATH"] = original

main()
