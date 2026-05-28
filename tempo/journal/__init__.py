"""Journaling: the validated boundary through which subjective entries are written.

Claude (or any caller) captures post-workout / rest-day reflections by calling the
validated :func:`tempo.journal.service.add_entry` entrypoint -- never by writing
SQL directly (JRNL-02; ARCHITECTURE Pattern 5 + Anti-Pattern 4). The service:

* validates the RPE range (1..10, integer) and the optional duration;
* resolves the activity by local date + sport (0 / 1 / many matches handled
  explicitly, see :func:`tempo.journal.service.resolve_activity`);
* computes sRPE (RPE x duration_minutes) from the linked activity's moving time
  (or an explicit duration) so a subjective load track exists when pace/HR load
  is unavailable (JRNL-03);
* inserts via parameterised SQL inside a transaction.

The CLI command ``tempo journal add`` is a thin wrapper over :func:`add_entry`.
"""

from tempo.journal.service import (
    ActivityMatch,
    JournalEntry,
    JournalError,
    MultipleActivitiesError,
    add_entry,
    compute_srpe,
    link_orphan_entries,
    list_entries,
    resolve_activity,
)

__all__ = [
    "ActivityMatch",
    "JournalEntry",
    "JournalError",
    "MultipleActivitiesError",
    "add_entry",
    "compute_srpe",
    "link_orphan_entries",
    "list_entries",
    "resolve_activity",
]
