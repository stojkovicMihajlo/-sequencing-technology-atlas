"""Short atomic reservations of unordered definition pairs, never model-time locks.

Claims expire for crash recovery. They do not assert a scientific relation and do
not change the registry generation. Completed comparisons remain the evidence.
"""

import math
import time

from genomics_discovery.curation.workflow_extraction.models import digest
from .proposal_queue import make_plan, comparisons
from .storage import connect, generation


def claim_pairs(path, submission_id, owner, *, ttl=1860, now=None, embeddings=None):
    if not owner or not math.isfinite(ttl) or ttl <= 0:
        raise ValueError("Comparison claims require an owner and positive finite ttl")
    stamp = time.time() if now is None else now
    for _ in range(16):
        with connect(path) as db:
            version = generation(db)
            plan = make_plan(db, submission_id, embeddings=embeddings)
            known = comparisons(db, submission_id, comparison_plan=plan, embeddings=embeddings)
        pending = [p for p in plan["pairs"] if p["pair_id"] not in known]
        # One orientation per unordered pair, also within this job's own cohort.
        unique = {digest(sorted((p["query_sha256"], p["target_sha256"]))): p for p in pending}
        with connect(path, write=True) as db:
            if generation(db) != version:
                continue
            db.execute("CREATE TABLE IF NOT EXISTS comparison_claims "
                       "(pair_key TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL)")
            db.execute("DELETE FROM comparison_claims WHERE expires<=?", (stamp,))
            acquired, waiting = [], []
            for key, pair in sorted(unique.items()):
                lease = db.execute("SELECT owner,expires FROM comparison_claims WHERE pair_key=?", (key,)).fetchone()
                if lease:
                    waiting.append({"pair_key": key, "owner": lease[0], "expires": lease[1]})
                    continue
                db.execute("INSERT INTO comparison_claims VALUES (?,?,?)", (key, owner, stamp + ttl))
                acquired.append(pair)
            return {"plan": {**plan, "pairs": acquired}, "waiting": waiting, "owner": owner}
    return {"plan": None, "waiting": [], "owner": owner, "retry": True}


def release_pairs(path, owner):
    with connect(path, write=True) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='comparison_claims'").fetchone():
            db.execute("DELETE FROM comparison_claims WHERE owner=?", (owner,))
