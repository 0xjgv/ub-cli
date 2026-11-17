"""Import crapper from the environment or the sibling checkout.

Function names and namespaces come from crapper so `.metrics/mutate` joins
the same operations uml-viewer joins to `.metrics/crap.edn`.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _sibling_src() -> Path:
    project = Path(__file__).resolve().parents[2]
    return project.parent / "crapper" / "src"


def ensure_crapper():
    try:
        from ub_cli._vendor import crapper
    except ImportError:
        sibling = _sibling_src()
        if not (sibling / "crapper").is_dir():
            raise ImportError(
                "crapper is not installed and was not found at "
                f"{sibling}. Clone github.com/unclebob/crapper next to mutator."
            ) from None
        sys.path.insert(0, str(sibling))
        from ub_cli._vendor import crapper
    import ub_cli._vendor.crapper.coverage
    from ub_cli._vendor import crapper
    import ub_cli._vendor.crapper.discover
    from ub_cli._vendor import crapper
    import ub_cli._vendor.crapper.languages
    from ub_cli._vendor import crapper
    import ub_cli._vendor.crapper.languages.treesitter
    from ub_cli._vendor import crapper
    import ub_cli._vendor.crapper.runners
    from ub_cli._vendor import crapper
    return crapper
