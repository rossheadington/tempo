"""Pure transforms: project the raw (bronze) layer into structured (silver) tables.

A transform reads verbatim JSON from ``raw_response`` and upserts typed rows into
the structured tables (``activity``, ``activity_stream``) and maintains the
``date_spine``. Transforms are **pure functions of the raw layer**: deterministic,
re-runnable, and they perform **zero network I/O**. This is what makes
``runos rederive`` a no-network DB pass (STORE-02; ARCHITECTURE Pattern 2).

The single most important correctness concern is *day attribution* -- which local
calendar day a record belongs to. That rule lives in :mod:`runos.transforms.bucketing`
and is applied here, never in a connector. See ``docs/DATE_BUCKETING.md``.
"""
