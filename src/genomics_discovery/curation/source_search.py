"""Search registered cached sources across jobs, never other agents' assay answers.

Results are retrieval leads with exact local-text offsets, not reviewed scientific
claims. This deliberately uses the existing lexical local-corpus scorer; it makes
no model calls, downloads, summaries, or changes to source/review artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path

from genomics_discovery.knowledge.retrieval import _score

from .prepared_sources import validate_packet
from .validation import (
    EVIDENCE_POLICY,
    NON_SOURCE_ARTIFACT_NAMES,
    _check_evidence_policy,
    _file_sha256,
    _load,
    _source_artifact_roles,
    _SourceHashCache,
    _sources,
    canonical_hash,
)

_TEXT_SUFFIXES = {".txt", ".md", ".html", ".htm", ".xml", ".json"}
_MAX_TEXT_BYTES = 4 * 1024 * 1024
_MAX_QUERY_CHARACTERS = 512
_MAX_QUERY_TERMS = 32
_TEXT_CACHE_ENTRIES = 4
_ANSWER_NAMES = NON_SOURCE_ARTIFACT_NAMES
_CATALOGUE_FORMAT = "curation_source_catalogue/1.0"
_SOURCE_METADATA_FIELDS = {"title", "primary_url", "inspected_url", "local_evidence", "sha256",
                           "artifact_hashes", "sections_inspected", "access_limits", "version",
                           "implementation_version", "text_renditions", "artifact_roles"}



def _passages(query: str, body: str, path: Path, digest: str) -> list[dict]:
    terms = sorted(set(re.findall(r"[a-z0-9]{3,}", query.casefold())))
    pattern = re.compile(r"\b(?:" + "|".join(map(re.escape, terms)) + r")\b", re.I)
    candidates = []
    for match in pattern.finditer(body):
        start, end = max(0, match.start() - 160), min(len(body), match.end() + 480)
        excerpt = body[start:end]
        candidate = {
            "path": str(path), "sha256": digest,
            "locator": {"type": "utf8_text_characters", "start": start, "end": end},
            "excerpt": excerpt, "score": _score(query, excerpt),
        }
        candidates = [p for p in candidates if p["locator"]["start"] != start]
        candidates.append(candidate)
        candidates.sort(key=lambda p: (-p["score"], p["locator"]["start"]))
        del candidates[2:]
        if len(candidates) == 2 and all(p["score"] == len(terms) for p in candidates):
            break  # Later passages cannot beat both maximal scores and earlier offsets.
    return candidates


def _sha(path: Path) -> str:
    return _file_sha256(path)


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def _run_metadata(run: Path) -> tuple[dict, dict]:
    if any(not (run / name).resolve().is_relative_to(run)
           for name in ("state.json", "config.json")):
        raise ValueError("Run metadata points outside the run directory")
    state, config = _load(run / "state.json"), _load(run / "config.json")
    if not isinstance(state, dict) or not isinstance(state.get("jobs"), dict):
        raise ValueError("Run state needs a jobs object")
    if not isinstance(config, dict) or not isinstance(config.get("atlas"), str):
        raise ValueError("Run configuration needs an atlas path")
    return state, config


def _document(artifact: Path, expected_hash=None) -> tuple[dict, str]:
    digest = _sha(artifact)
    if expected_hash is not None and expected_hash != digest:
        raise ValueError("Saved artifact sha256 mismatch")
    document = _load(artifact)
    if not isinstance(document, dict):
        raise ValueError("Saved artifact must be a JSON object")
    if _sha(artifact) != digest:
        raise ValueError("Saved artifact changed during search")
    if not isinstance(document.get("sources", []), list):
        raise ValueError("Malformed source register")
    return document, digest


def _source_registration(source: dict, base: Path, roots: tuple[Path, ...],
                         artifact: Path, hash_file=None) -> dict:
    """Reject known answer files before hashing or parsing them as source evidence."""
    paths = source.get("source_metadata", {}).get("local_evidence", [])
    paths = [paths] if isinstance(paths, str) else paths
    if not isinstance(paths, list):
        raise ValueError("Malformed local_evidence")
    for value in paths:
        if isinstance(value, str):
            path = (base / value).resolve()
            if path.name in _ANSWER_NAMES or path == artifact:
                raise ValueError("Agent answer is not a source")
    # Untyped historical records remain retrieval leads, never silently classified.
    # Explicit roles, when present, must obey the same source contract as outputs.
    policy = (EVIDENCE_POLICY if "artifact_roles" in source.get("source_metadata", {}) else None)
    return _sources([source], base, roots, hash_file=hash_file, evidence_policy=policy)


def _checkpoint(job: dict, roots: tuple[Path, ...]) -> tuple[Path, str] | None:
    if not job.get("extraction"):
        return None
    path = Path(job["extraction"]).resolve()
    if not _inside(path, roots):
        raise ValueError("Checkpoint extraction outside allowed atlas/run roots")
    digest = job.get("extraction_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Checkpoint extraction needs a pinned sha256")
    return path, digest


def _review_base(run: Path, attempts: list, index: int, review: dict,
                 checkpoint: tuple[Path, str] | None = None) -> Path | None:
    """Review-relative source paths use the hash-matched extraction directory."""
    candidates = []
    for prior in reversed(attempts[:index]):
        if prior.get("role") != "extractor":
            continue
        candidate = (Path(prior["directory"]).resolve() / "extraction.json").resolve()
        if candidate.is_relative_to(run):
            candidates.append((candidate, prior.get("artifact_sha256")))
    if checkpoint is not None:
        candidates.append(checkpoint)
    for candidate, expected_hash in candidates:
        try:
            document, _ = _document(candidate, expected_hash)
            profiles = document.get("profiles", [])
            if (isinstance(profiles, list) and len(profiles) == 1
                    and canonical_hash(profiles[0]) == review.get("profile_sha256")):
                return candidate.parent
        except (OSError, ValueError, TypeError, UnicodeError):
            continue
    return None


def _registered_sources(run: Path, state: dict, roots: tuple[Path, ...], issues: list,
                        hash_file=None):
    """Yield validated registers and metadata pointers without exposing assay answers."""
    for method, job in state["jobs"].items():
        attempts = job.get("attempts", [])
        try:
            checkpoint = _checkpoint(job, roots)
        except (OSError, ValueError, TypeError) as exc:
            issues.append({"method": method, "reason": str(exc)})
            checkpoint = None
        candidates, attempted_paths = [], set()
        for index, attempt in enumerate(attempts):
            directory = Path(attempt["directory"]).resolve()
            if not directory.is_relative_to(run):
                issues.append({"method": method, "reason": "Attempt outside run directory"})
                continue
            role = attempt.get("role")
            if role not in {"extractor", "independent_verifier"}:
                issues.append({"method": method, "reason": "Unknown attempt role"})
                continue
            name = "review.json" if role == "independent_verifier" else "extraction.json"
            artifact = (directory / name).resolve()
            if not artifact.is_relative_to(run):
                issues.append({"method": method, "reason": "Artifact outside run directory"})
                continue
            attempted_paths.add(artifact)
            candidates.append((artifact, attempt.get("artifact_sha256"), role, index))
        if checkpoint is not None and checkpoint[0] not in attempted_paths:
            candidates.append((*checkpoint, "extractor", len(attempts)))
        for artifact, expected_hash, role, index in candidates:
            if not artifact.exists():
                continue
            try:
                document, digest = _document(artifact, expected_hash)
            except (OSError, ValueError, UnicodeError) as exc:
                issues.append({"artifact": str(artifact), "reason": str(exc)})
                continue
            review_base = (_review_base(run, attempts, index, document, checkpoint)
                           if role == "independent_verifier" else None)
            for source in document.get("sources", []):
                try:
                    base = artifact.parent
                    if role == "independent_verifier":
                        paths = source.get("source_metadata", {}).get("local_evidence", [])
                        paths = [paths] if isinstance(paths, str) else paths
                        if not isinstance(paths, list):
                            raise ValueError("Malformed local_evidence")
                        if any(isinstance(p, str) and not Path(p).is_absolute() for p in paths):
                            if review_base is None:
                                raise ValueError(
                                    "Relative reviewer sources need matching extraction")
                            base = review_base
                        else:
                            base = review_base or run
                    register = _source_registration(source, base, roots, artifact, hash_file)
                    # Also reject invalid Unicode before emitting snapshot entries.
                    canonical_hash(source)
                except (OSError, ValueError, TypeError, AttributeError, UnicodeError) as exc:
                    issues.append({"artifact": str(artifact), "reason": str(exc)})
                    continue
                origin = {"run": str(run), "base": str(base), "artifact": str(artifact),
                          "artifact_sha256": digest, "method_id": method, "role": role}
                yield source, register, origin


def build_source_catalogue(source_runs: tuple[Path, ...], atlas: Path, *,
                           evidence_policy=None) -> dict:
    """Read-only metadata snapshot of registered evidence from same-atlas runs.

    External runs must reside inside the target atlas and declare that same atlas.
    Per-source failures are retained as issues; invalid run boundaries reject the
    request. The caller writes and pins the returned snapshot before workers run.
    """
    _check_evidence_policy(evidence_policy)
    atlas = Path(atlas).resolve()
    entries, issues, runs, seen = [], [], [], set()
    hash_file = _SourceHashCache()
    for supplied_run in source_runs:
        run = Path(supplied_run).resolve()
        if not run.is_relative_to(atlas):
            raise ValueError("Source run must be inside the target atlas")
        state, config = _run_metadata(run)
        if Path(config["atlas"]).resolve() != atlas:
            raise ValueError("Source run declares a different atlas")
        run_record = {"path": str(run), "config_sha256": _sha(run / "config.json"),
                      "state_sha256": _sha(run / "state.json")}
        if run_record in runs:
            continue
        runs.append(run_record)
        for source, _, origin in _registered_sources(run, state, (atlas,), issues, hash_file):
            metadata = {key: deepcopy(value) for key, value in source["source_metadata"].items()
                        if key in _SOURCE_METADATA_FIELDS}
            entry = {"source_id": source["source_id"], "source_metadata": metadata,
                     "origin": origin}
            digest = canonical_hash(entry)
            if digest not in seen:
                seen.add(digest)
                entries.append(entry)
        if (_sha(run / "config.json") != run_record["config_sha256"]
                or _sha(run / "state.json") != run_record["state_sha256"]):
            raise ValueError("Source run metadata changed while building catalogue")
    return {"format_version": _CATALOGUE_FORMAT, "atlas": str(atlas),
            "evidence_policy": evidence_policy,
            "source_runs": runs, "entries": entries, "issues": issues,
            "scope": "Registered source metadata and immutable file pointers only; "
                     "no extracted protocol steps or scientific review conclusions."}


def _catalogue_sources(run: Path, config: dict, roots: tuple[Path, ...], issues: list,
                       hash_file=None):
    index = (run / "shared_sources" / "index.json").resolve()
    expected = config.get("source_catalogue_sha256")
    if not index.is_relative_to(run):
        issues.append({"artifact": str(index), "reason": "Source catalogue outside run directory"})
        return
    if not index.is_file():
        if expected is not None:
            issues.append({"artifact": str(index), "reason": "Pinned source catalogue is missing"})
        return
    try:
        digest = _sha(index)
        if expected is not None and expected != digest:
            raise ValueError("Source catalogue sha256 mismatch")
        catalogue = _load(index)
        if _sha(index) != digest:
            raise ValueError("Source catalogue changed during search")
        if catalogue.get("format_version") != _CATALOGUE_FORMAT:
            return  # Legacy discovery indexes retain their existing reader contract.
        _check_evidence_policy(catalogue.get("evidence_policy"))
        atlas = Path(config["atlas"]).resolve()
        if Path(catalogue["atlas"]).resolve() != atlas:
            raise ValueError("Source catalogue declares a different atlas")
        if not isinstance(catalogue.get("entries"), list):
            raise ValueError("Malformed source catalogue entries")
        allowed_runs = {}
        for record in catalogue["source_runs"]:
            source_run = Path(record["path"]).resolve()
            if not source_run.is_relative_to(atlas):
                raise ValueError("Catalogue source run outside target atlas")
            config_path = (source_run / "config.json").resolve()
            if not config_path.is_relative_to(source_run):
                raise ValueError("Catalogue source configuration outside its run")
            source_config = _load(config_path)
            if (Path(source_config["atlas"]).resolve() != atlas
                    or _sha(config_path) != record["config_sha256"]):
                raise ValueError("Catalogue source run configuration changed")
            allowed_runs[str(source_run)] = source_run
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        issues.append({"artifact": str(index), "reason": str(exc)})
        return
    artifact_hashes = {}
    for entry in catalogue["entries"]:
        try:
            origin = entry["origin"]
            source_run = allowed_runs[origin["run"]]
            base, artifact = Path(origin["base"]).resolve(), Path(origin["artifact"]).resolve()
            if not _inside(base, roots) or not _inside(artifact, roots):
                raise ValueError("Catalogue source origin outside allowed atlas/run roots")
            if origin["role"] not in {"extractor", "independent_verifier"}:
                raise ValueError("Unknown catalogue source role")
            if artifact not in artifact_hashes:
                artifact_hashes[artifact] = _sha(artifact)
            if artifact_hashes[artifact] != origin["artifact_sha256"]:
                raise ValueError("Catalogue origin artifact sha256 mismatch")
            # The source run association is provenance; checkpoint artifacts may
            # be elsewhere inside this same atlas, under the frozen root policy.
            if not source_run.is_relative_to(atlas):
                raise ValueError("Catalogue source run outside target atlas")
            source = {"source_id": entry["source_id"], "source_metadata": entry["source_metadata"]}
            register = _source_registration(source, base, roots, artifact, hash_file)
            yield source, register, origin
        except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
            issues.append({"artifact": str(index), "reason": str(exc)})


def _prepared_sources(run: Path, state: dict, config: dict, issues: list):
    """Search frozen document leads without inventing a source-inspection register."""
    expected = config.get("prepared_sources_sha256")
    if expected is None:
        return  # Legacy/unprepared runs do not trust an unpinned packet on disk.
    packet_path = (run / "prepared_sources.json").resolve()
    if not packet_path.is_relative_to(run):
        issues.append({"artifact": str(packet_path),
                       "reason": "Prepared sources outside run directory"})
        return
    try:
        digest = _sha(packet_path)
        if expected != digest:
            raise ValueError("Prepared sources sha256 mismatch")
        packet = _load(packet_path)
        atlas = Path(config["atlas"]).resolve()
        # The preparation gate checks the exact packet shape, atlas boundaries,
        # file roles and source bytes. It supplies no scientific reading claim.
        validate_packet(packet, atlas, list(state["jobs"]))
        canonical_hash(packet)  # Reject invalid Unicode before emitting candidates.
        if _sha(packet_path) != digest:
            raise ValueError("Prepared sources changed during search")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        issues.append({"artifact": str(packet_path), "reason": str(exc)})
        return
    for method, leads in packet["methods"].items():
        for lead in leads:
            files = lead["files"]
            source = {"source_id": lead["source_id"], "source_metadata": {
                "title": lead["title"], "primary_url": lead["url"],
                "local_evidence": [f["path"] for f in files],
                "sha256": {f["path"]: f["sha256"] for f in files},
                "artifact_roles": {f["path"]: f["role"] for f in files},
            }}
            register = {lead["source_id"]: {Path(f["path"]).resolve(): f["sha256"]
                                            for f in files}}
            origin = {"run": str(run), "base": str(atlas), "artifact": str(packet_path),
                      "artifact_sha256": digest, "method_id": method,
                      "role": "prepared_source_packet", "reading_status": "not_attested"}
            yield source, register, origin


def search_cached_sources(run: Path, query: str, *, limit: int = 5) -> dict:
    """Find saved source registers and pinned prepared documents before extraction.

    Only source registers are exposed, not profile steps or reviewer conclusions.
    Every returned source passes the existing file/root/hash checks. These checks
    do not establish document identity, entailment, or applicability to a method.
    """
    if not isinstance(query, str) or not re.search(r"[a-z0-9]{3,}", query, re.I):
        raise ValueError("Query needs at least one term of three letters/digits")
    if len(query) > _MAX_QUERY_CHARACTERS or len(set(re.findall(
            r"[a-z0-9]{3,}", query.casefold()))) > _MAX_QUERY_TERMS:
        raise ValueError("Query exceeds the 512-character or 32-term search budget")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("limit must be an integer from 1 to 20")
    run = Path(run).resolve()
    state, config = _run_metadata(run)
    evidence_policy = config.get("evidence_policy")
    _check_evidence_policy(evidence_policy)
    roots = (run, Path(config["atlas"]).resolve())
    seen, text_cache, hits, issues = set(), OrderedDict(), {}, []
    checked_sources = 0
    hash_file = _SourceHashCache()
    candidates = _registered_sources(run, state, roots, issues, hash_file)
    catalogue = _catalogue_sources(run, config, roots, issues, hash_file)
    prepared = _prepared_sources(run, state, config, issues)
    for group in (candidates, catalogue, prepared):
        for source, register, source_origin in group:
            artifact = source_origin["artifact"]
            key = (artifact, canonical_hash(source))
            if key in seen:
                continue
            seen.add(key)
            checked_sources += 1
            sid, meta = source["source_id"], source["source_metadata"]
            files = register[sid]
            roles = _source_artifact_roles(meta, files, Path(source_origin["base"]), sid)
            metadata_only = evidence_policy == EVIDENCE_POLICY and not roles
            evidence_status = ("unclassified_legacy_metadata_only" if metadata_only else
                               "declared_scientific_artifacts" if roles else "unclassified_legacy")
            identity = canonical_hash({"url": meta["primary_url"],
                                       "version": meta.get("version"),
                                       "hashes": sorted(set(files.values())),
                                       "evidence_status": evidence_status})
            origin = {**source_origin, "source_id": sid, "status": "discovery_lead_only"}
            if identity in hits:
                hits[identity]["found_in"].append(origin)
                continue
            search_metadata = meta
            if metadata_only:
                search_metadata = {key: meta[key] for key in (
                    "title", "primary_url", "inspected_url", "version", "implementation_version",
                    "local_evidence") if key in meta}
            metadata_score = _score(query, json.dumps(search_metadata, ensure_ascii=False))
            passages, limitations = [], []
            for path, digest in files.items():
                if metadata_only:
                    limitations.append({
                        "path": str(path),
                        "reason": "Unclassified legacy artifact: metadata-only discovery. "
                                  "Inspect origin/content before registering a scientific role.",
                    })
                    continue
                if path.name in _ANSWER_NAMES:
                    limitations.append({"path": str(path),
                                        "reason": "Agent answer is not a source"})
                    continue
                if path.suffix.lower() not in _TEXT_SUFFIXES:
                    continue
                cache_key = (str(path), digest)
                if cache_key not in text_cache:
                    try:
                        with path.open("rb") as handle:
                            raw = handle.read(_MAX_TEXT_BYTES + 1)
                    except OSError as exc:
                        limitations.append({"path": str(path), "reason": str(exc)})
                        continue
                    if len(raw) > _MAX_TEXT_BYTES:
                        limitations.append({"path": str(path),
                                            "reason": "Text exceeds search byte cap"})
                        continue
                    # Guard a file replacement between register validation and search.
                    if hashlib.sha256(raw).hexdigest() != digest:
                        limitations.append({"path": str(path),
                                            "reason": "Source changed during search"})
                        continue
                    try:
                        text_cache[cache_key] = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        limitations.append({"path": str(path), "reason": "Not UTF-8 text"})
                        continue
                    if len(text_cache) > _TEXT_CACHE_ENTRIES:
                        text_cache.popitem(last=False)
                else:
                    text_cache.move_to_end(cache_key)
                passages.extend(_passages(query, text_cache[cache_key], path, digest))
            score = metadata_score + max((p["score"] for p in passages), default=0)
            if score:
                hits[identity] = {
                    "title": meta["title"], "primary_url": meta["primary_url"],
                    "version": meta.get("version"), "score": score,
                    "evidence_status": evidence_status,
                    "files": [{"path": str(p), "sha256": h,
                               **({"artifact_role": roles[p]} if p in roles else {})}
                              for p, h in files.items()],
                    "passages": sorted(passages, key=lambda p: -p["score"])[:2],
                    "access_limits": meta.get("access_limits", []),
                    "search_limits": limitations, "found_in": [origin],
                }
    ranked = sorted(hits.values(), key=lambda h: (-h["score"], h["title"]))
    return {
        "query": query, "run": str(run), "hits": ranked[:limit],
        "matched_sources": len(ranked), "checked_source_registers": checked_sources,
        "issues": issues,
        "scope": "Lexical discovery over registered cached sources, including unreviewed drafts "
                 "and pinned prepared documents. Preparation does not attest source inspection. "
                 "No inferred protocol compatibility or scientific approval. Reopen source/version "
                 "and relevant passages before use. Not exhaustive: unregistered downloads and "
                 "binary content without a registered UTF-8 rendition are not text-searched. "
                 "Under the scientific-source policy, untyped legacy artifacts supply metadata "
                 "leads only. Declared roles and hashes do not prove authorship or entailment.",
    }
