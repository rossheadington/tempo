"""First-run setup wizard package.

Public entrypoint: ``from runos.setup import run_wizard``. Submodules
(``env_io``, ``state``, ``prompts``, ``wizard``) are imported directly by
callers that need them — only :func:`run_wizard` is re-exported here.
"""

from __future__ import annotations

from runos.setup.wizard import run_wizard

__all__ = ["run_wizard"]
