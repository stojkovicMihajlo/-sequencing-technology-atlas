"""Row storage with snapshot-compatible exports and optimistic, brief commits.

Legacy databases remain readable. Migration writes a new database; it never edits
an existing scientific archive. Model calls never run inside these transactions.
"""

import json
import sqlite3
from contextlib import contextmanager

SECTIONS = ("concepts", "assignments", "decisions")


@contextmanager
def connect(path, *, write=False):
    db = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
                         uri=True, timeout=30)
    try:
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def normalized(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='registry_meta'").fetchone())


def create(db, snapshot):
    db.execute("CREATE TABLE registry_meta (id INTEGER PRIMARY KEY, generation INTEGER NOT NULL, payload TEXT NOT NULL)")
    for section in SECTIONS:
        db.execute(f"CREATE TABLE {section} (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    db.execute("CREATE TABLE submissions (id TEXT PRIMARY KEY, payload TEXT NOT NULL, review TEXT, result TEXT)")
    db.execute("CREATE TABLE comparison_reports (id TEXT PRIMARY KEY, submission_id TEXT NOT NULL, payload TEXT NOT NULL)")
    db.execute("CREATE TABLE candidate_terms (owner TEXT NOT NULL, field TEXT NOT NULL, term TEXT NOT NULL, PRIMARY KEY(owner,field,term))")
    db.execute("CREATE INDEX candidate_lookup ON candidate_terms(field,term,owner)")
    db.execute("INSERT INTO registry_meta VALUES (1,0,'{}')")
    write_snapshot(db, snapshot)


def read_snapshot(db):
    if not normalized(db):
        return json.loads(db.execute("SELECT payload FROM registry WHERE id=1").fetchone()[0])
    meta = json.loads(db.execute("SELECT payload FROM registry_meta WHERE id=1").fetchone()[0])
    result = meta["root"]
    for section in meta["sections"]:
        result[section] = {key: json.loads(value) for key, value in
                           db.execute(f"SELECT id,payload FROM {section} ORDER BY id")}
    return result


def generation(db):
    if not normalized(db):
        raise ValueError("Legacy registry is read-only; migrate to a new database before writing")
    return db.execute("SELECT generation FROM registry_meta WHERE id=1").fetchone()[0]


def write_snapshot(db, snapshot):
    """Only changed rows are written. Caller owns validation and transaction."""
    meta = {"root": {k: v for k, v in snapshot.items() if k not in SECTIONS},
            "sections": [k for k in SECTIONS if k in snapshot]}
    for section in SECTIONS:
        previous = dict(db.execute(f"SELECT id,payload FROM {section}"))
        incoming = snapshot.get(section, {})
        for key in previous.keys() - incoming.keys():
            db.execute(f"DELETE FROM {section} WHERE id=?", (key,))
            if section == "concepts":
                db.execute("DELETE FROM candidate_terms WHERE owner=?", ("concept:" + key,))
        for key, value in incoming.items():
            payload = json.dumps(value, sort_keys=True)
            if previous.get(key) != payload:
                db.execute(f"INSERT INTO {section} VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                           (key, payload))
                if section == "concepts":
                    from .candidate_index import index_definition
                    index_definition(db, "concept:" + key, value)
    db.execute("UPDATE registry_meta SET generation=generation+1,payload=? WHERE id=1", (json.dumps(meta),))


def migrate(source, target):
    from .integrity import validate_snapshot
    from .store import Registry
    from genomics_discovery.curation.workflow_extraction.models import digest
    snapshot = Registry(source).snapshot()
    target = target.resolve()
    # Exclusive creation prevents silently replacing a previous registry.
    with target.open("xb"):
        pass
    try:
        with sqlite3.connect(target) as db:
            create(db, snapshot)
        restored = Registry(target).snapshot()
        validate_snapshot(restored)
        if restored != snapshot:
            raise ValueError("Migration changed the scientific snapshot")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"database": str(target), "snapshot_sha256": digest(snapshot), "source_unchanged": True}
