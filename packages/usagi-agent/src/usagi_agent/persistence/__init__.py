"""Persistence layer: Port interfaces + InMemory / SQLite implementations.

The Port surface lives in :mod:`usagi_agent.persistence.ports`. Concrete backends are
selected by :class:`usagi_agent.persistence.backend.PersistenceBackend` at Bootstrap.
"""
