"""SQLite durable backend (design §10.6, §24.1, M0B).

Schema is concrete (see :mod:`usagi_agent.persistence.sqlite.schema`). The Python
implementations are wired behind the InMemory backend for now — the fencing gate, CAS on
``version`` vs ``lease_version``, single-DB transaction boundary and ThreadControlBinding
uniqueness are all exercised by the InMemory + gate-verifier path until the durable impl
lands. See TODO(§10.6) in :mod:`usagi_agent.persistence.backend`.
"""
