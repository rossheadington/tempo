"""Ordered SQL schema migrations applied by :mod:`runos.db`.

Each migration is a ``NNNN_*.sql`` file. They are applied in filename order and
the integer ``PRAGMA user_version`` tracks how many have run, so a connect-time
``migrate()`` only ever applies the missing ones, in a transaction.
"""
