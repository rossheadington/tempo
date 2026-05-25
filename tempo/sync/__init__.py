"""Incremental-sync bookkeeping: watermarks and the backfill cursor.

Everything that decides *what to fetch next* lives here, not smeared across the
connectors. A connector is handed a ``since`` and a cursor; it is not
responsible for remembering them.
"""
