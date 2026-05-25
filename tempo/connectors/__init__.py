"""Source connectors: the only network boundary in Tempo.

A connector owns all I/O with an external API (auth, paging, rate limits,
retries) and writes **verbatim responses to the raw store only** -- never to a
structured table. Structured tables are a pure projection of raw, produced by
transforms in a later phase, so the system can re-derive everything from stored
raw without re-fetching.

Phase 2 ships the Strava connector. Phase 6 adds Garmin behind the same
:class:`~tempo.connectors.base.Connector` interface.
"""
