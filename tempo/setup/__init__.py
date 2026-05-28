"""First-run setup wizard package.

Owns orchestration, ``.env`` I/O, state detection, and step dispatch for the
``tempo setup`` command. The wizard itself lives in ``wizard.py`` (added in
Plan 14-02); this package only exposes pure helpers that the wizard composes.

Submodules:

- :mod:`tempo.setup.env_io` — atomic ``.env`` read/write mirroring the
  rotating-token atomic-write template in :mod:`tempo.connectors.tokens`.
- :mod:`tempo.setup.state` — pure read-only install-state detection.

No side-effect imports here: callers import the submodules directly so this
package marker stays cheap and import-safe.
"""

from __future__ import annotations

__all__: list[str] = []
