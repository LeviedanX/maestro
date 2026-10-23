"""Deprecated compatibility entry point for the isolated canonical pipeline."""

from __future__ import annotations

import sys
from typing import Any
import warnings

from .run_research_pipeline import main as canonical_main
from .run_research_pipeline import run_research_pipeline


LEGACY_OUTPUT_POLICY = "delegates_to_isolated_canonical_pipeline"
DEPRECATION_MESSAGE = (
    "src.run_pipeline is deprecated and no longer writes shared production artifacts; "
    "use `python -m src.run_research_pipeline`."
)


def run_pipeline(**kwargs: Any) -> dict[str, Any]:
    """Delegate callers to the canonical run-scoped implementation."""

    warnings.warn(DEPRECATION_MESSAGE, FutureWarning, stacklevel=2)
    return run_research_pipeline(**kwargs)


def main() -> int:
    print(f"WARNING: {DEPRECATION_MESSAGE}", file=sys.stderr)
    return canonical_main()


if __name__ == "__main__":
    raise SystemExit(main())
