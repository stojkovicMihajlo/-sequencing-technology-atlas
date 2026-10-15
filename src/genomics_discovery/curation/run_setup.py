"""Freeze method tasks, source packets, scope and budgets before execution."""

from __future__ import annotations

import math
import os
import re
from pathlib import Path

from .codex_worker import DEFAULT_EFFORT, DEFAULT_MODEL
from .contracts import CONTRACT_VERSION, load_contracts
from .prompts import SOURCE_CHECKLIST_POLICIES
from .run_state import now, read, sha, summarize, write
from .validation import EVIDENCE_POLICY, canonical_hash


def _source_only_paper_lead(paper: dict) -> dict:
    """Retain discovery metadata without passing earlier scientific conclusions."""
    identity = ("paper_id", "pmid", "pmcid", "title", "doi", "url", "publication_year")
    locations = ("local_full_text_path", "local_excerpt_path", "full_text_url",
                 "reviewed_full_text_url", "sha256", "full_text_sha256", "access_status")
    lead = {key: paper[key] for key in identity if key in paper}
    acquisition = paper.get("acquisition")
    if isinstance(acquisition, dict):
        lead["acquisition"] = {key: acquisition[key] for key in (
            *identity, *locations, "acquired_at_utc", "is_open_access", "supplement_links"
        ) if key in acquisition}
    cached_sources = [
        {key: review[key] for key in (*identity, *locations) if key in review}
        for review in paper.get("reviews", []) if isinstance(review, dict)
    ]
    if any(cached_sources):
        lead["cached_source_leads"] = [source for source in cached_sources if source]
    return lead


def initialize(
    run: Path,
    atlas: Path,
    *,
    method_ids: list[str] | None = None,
    limit: int | None = None,
    workers: int = 10,
    reviewers: int = 2,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    timeout: float | None = 1800,
    max_revisions: int = 2,
    max_attempts: int = 2,
    source_only: bool = False,
    with_evolution: bool = False,
    incremental_reviews: bool = False,
    source_runs: list[Path] | None = None,
    evidence_policy: str | None = EVIDENCE_POLICY,
    continue_from: Path | None = None,
    prepared_sources: Path | None = None,
    review_scope: Path | None = None,
    reuse_source_checklist: bool = False,
    source_checklist_policy: str = "independent",
    connection_reviews: bool = False,
    scientific_detail_reviews: bool = False,
    allow_timeout_correction_leads: bool = False,
    allow_source_discovery: bool = False,
) -> dict:
    run, atlas = run.resolve(), atlas.resolve()
    if run.exists():
        raise ValueError("Run directory already exists; use run/status to resume or inspect it")
    if not 2 <= workers <= 100 or not 1 <= reviewers < workers:
        raise ValueError("workers must be 2..100, with 1 <= reviewers < workers")
    if (not 0 <= max_revisions <= 5 or not 1 <= max_attempts <= 5
            or timeout is not None and (type(timeout) not in {int, float}
                                        or not math.isfinite(timeout) or timeout <= 0)):
        raise ValueError("Invalid revision, attempt or timeout bounds")
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("Unsupported reasoning effort")
    if type(allow_source_discovery) is not bool:
        raise ValueError("allow_source_discovery must be boolean")
    if evidence_policy not in {None, EVIDENCE_POLICY}:
        raise ValueError("Unsupported scientific evidence policy")
    if not isinstance(connection_reviews, bool):
        raise ValueError("connection_reviews must be boolean")
    if not isinstance(scientific_detail_reviews, bool):
        raise ValueError("scientific_detail_reviews must be boolean")
    if source_checklist_policy not in SOURCE_CHECKLIST_POLICIES:
        raise ValueError("Unsupported source checklist policy")
    if source_checklist_policy == "reviewer_source_audit":
        if not (source_only and prepared_sources is not None and review_scope is not None
                and connection_reviews and scientific_detail_reviews
                and evidence_policy == EVIDENCE_POLICY and not incremental_reviews):
            raise ValueError("reviewer_source_audit requires scoped prepared source_only "
                             "runs with strict evidence and fresh detail/connection reviews")
        if reuse_source_checklist:
            raise ValueError("reviewer_source_audit cannot reuse a separate source checklist")
    if scientific_detail_reviews and not (
            connection_reviews and source_only and prepared_sources is not None
            and review_scope is not None and evidence_policy == EVIDENCE_POLICY
            and not incremental_reviews):
        raise ValueError("Scientific detail review requires source_only, prepared_sources, "
                         "review_scope and fresh per-connection reviews")
    if not isinstance(allow_timeout_correction_leads, bool):
        raise ValueError("allow_timeout_correction_leads must be boolean")
    if allow_timeout_correction_leads and (continue_from is None or incremental_reviews):
        raise ValueError("Timeout correction leads require a continuation and fresh full reviews")
    if connection_reviews and incremental_reviews:
        raise ValueError("Per-connection checks currently require fresh full reviews")
    if review_scope is not None and (not source_only or prepared_sources is None
                                     or incremental_reviews):
        raise ValueError("Review scope requires source_only, prepared_sources and fresh reviews")
    if continue_from is not None and (not method_ids or limit is not None):
        raise ValueError("Continuation requires explicit method IDs and no implicit limit")
    if reuse_source_checklist and (continue_from is None or prepared_sources is None
                                  or review_scope is None):
        raise ValueError("Checklist reuse requires a scoped prepared-source continuation")
    graph_path = atlas / "output/knowledge_graph.json"
    graph = read(graph_path)
    methods = {m["id"]: m for m in graph["methods"]}
    if method_ids is None:
        manifest_path = atlas / "work/component_curation_20261008/curation_manifest.json"
        if not manifest_path.is_file():
            raise ValueError("Select explicit method IDs; "
                             "no legacy selection manifest is available")
        manifest = read(manifest_path)
        method_ids = [r["method_id"] for r in manifest["rows"]
                      if r["status"] == "pending_detailed_review"]
    if len(method_ids) != len(set(method_ids)) or set(method_ids) - set(methods):
        raise ValueError("Duplicate or unknown method IDs")
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        method_ids = method_ids[:limit]
    if not method_ids:
        raise ValueError("No pending methods selected")
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", m) for m in method_ids):
        raise ValueError("Method IDs must be safe directory names")
    scope_document = None
    if review_scope is not None:
        from .coverage import load_scopes
        scope_document = load_scopes(review_scope, method_ids)
    packet = None
    if prepared_sources is not None:
        from .prepared_sources import load_packet
        packet = load_packet(prepared_sources, atlas, method_ids)
    seeds = {}
    if continue_from is not None:
        from .continuation import prepare_continuation
        continue_from = Path(continue_from).resolve()
        seeds = prepare_continuation(continue_from, atlas, method_ids,
            allow_timeout_correction_leads=allow_timeout_correction_leads)
        if (read(continue_from / "config.json").get("review_scope_sha256") is not None
                and scope_document is None):
            raise ValueError("Scoped continuation must retain its frozen structured review scope")
        if scope_document is not None:
            old_scope = continue_from / "review_scope.json"
            if (not old_scope.is_file() or any(
                    read(old_scope).get("methods", {}).get(mid) != scope_document["methods"][mid]
                    for mid in method_ids)):
                raise ValueError("Scoped continuation requires identical prior structured scope; "
                                 "legacy migration needs a separate explicit scope audit")
        source_runs = list(dict.fromkeys([
            continue_from, *(Path(p).resolve() for p in (source_runs or []))]))
    catalogue = None
    if source_runs:
        from .source_search import build_source_catalogue
        catalogue = build_source_catalogue(tuple(Path(p).resolve() for p in source_runs), atlas,
                                           evidence_policy=evidence_policy)
    if seeds:
        from .continuation import validate_seed
        for seed in seeds.values():
            validate_seed(seed)
    contracts = load_contracts()
    run.mkdir(parents=True)
    write(run / "contracts.json", contracts)
    if scope_document is not None:
        write(run / "review_scope.json", scope_document)
    if packet is not None:
        write(run / "prepared_sources.json", packet)
    if with_evolution:
        write(run / "method_registry.json", {"methods": [
            {k: m[k] for k in ("id", "name", "aliases", "year") if k in m}
            for m in methods.values()]})
    papers = {p["paper_id"]: p for p in graph["papers"]}
    tasks = {}
    for mid in method_ids:
        method = methods[mid]
        task = ({key: method[key] for key in (
            "id", "name", "aliases", "original_categories", "year", "origin_paper_ids", "paper_ids"
        ) if key in method} if source_only else dict(method))
        if source_only:
            task["source_only"] = True
        if evidence_policy is not None:
            task["evidence_policy"] = evidence_policy
        task["paper_leads"] = [
            _source_only_paper_lead(papers[p]) if source_only else papers[p]
            for p in method.get("paper_ids", []) if p in papers
        ]
        task["read_only_source_roots"] = [str(atlas / "papers")]
        if not source_only:
            task["read_only_source_roots"].append(str(atlas / "work"))
        task["cached_paper_directories"] = [str(atlas / "papers" / p.removeprefix("pmid:"))
            for p in method.get("paper_ids", []) if p.startswith("pmid:")
            and (atlas / "papers" / p.removeprefix("pmid:")).exists()]
        task["workspace_root_for_legacy_relative_paths"] = str(atlas.parent.parent)
        if packet is not None:
            task["prepared_sources"] = packet["methods"][mid]
        if allow_source_discovery:
            task["allow_source_discovery"] = True
        if scope_document is not None:
            task["review_scope"] = scope_document["methods"][mid]
        task["source_checklist_policy"] = source_checklist_policy
        if connection_reviews:
            task["connection_reviews_required"] = True
        if scientific_detail_reviews:
            task["scientific_detail_policy"] = "scientific-detail/1.0"
        if with_evolution:
            task["evolution_required"] = True
            task["method_registry_path"] = str(run / "method_registry.json")
        path = run / "jobs" / mid / "input.json"
        write(path, task)
        tasks[mid] = {"status": "pending", "revision": 0, "attempts": [],
                      "input_sha256": sha(path), "extraction": None, "review": None,
                      "feedback": None}
        if mid in seeds:
            # Repair context belongs to the extractor, not the common task seen
            # by a fresh reviewer. No prior acceptance or review is inherited.
            tasks[mid]["seed"] = seeds[mid]
            if seeds[mid].get("provisional_critique", {}).get("transport_provenance"):
                tasks[mid]["force_full_review_revision"] = 0
            origin = seeds[mid].get("source_checklist")
            if (reuse_source_checklist and origin is not None
                    and scope_document is not None and packet is not None
                    and origin["scope_sha256"] == canonical_hash(scope_document["methods"][mid])
                    and origin["source_packet_method_sha256"] == canonical_hash(
                        packet["methods"][mid])):
                copied = run / "jobs" / mid / "reused_source_checklist/checklist.json"
                copied.parent.mkdir()
                copied.write_bytes(Path(origin["path"]).read_bytes())
                if sha(copied) != origin["sha256"]:
                    raise ValueError("Source checklist changed during continuation copy")
                tasks[mid].update(source_checklist=str(copied),
                                  source_checklist_sha256=sha(copied))
    config = {"format_version": "atlas_curation_run/1.0", "created_at": now(),
              "atlas": str(atlas), "atlas_revision": graph["revision_id"],
              "atlas_sha256": sha(graph_path), "workers": workers, "reviewers": reviewers,
              "model": model, "effort": effort, "timeout_seconds": timeout,
              "max_revisions": max_revisions, "max_attempts": max_attempts,
              "source_only": source_only,
              "evidence_policy": evidence_policy,
              "contracts_sha256": sha(run / "contracts.json"),
              "contract_resource_version": CONTRACT_VERSION,
              "implementation_hashes": {p.name: sha(p) for p in Path(__file__).parent.glob("*.py")},
              "auth": {"mode": "existing Codex CLI authentication",
                       "api_key_environment_present": bool(os.environ.get("OPENAI_API_KEY")
                                                            or os.environ.get("CODEX_API_KEY")),
                       "credentials_archived": False}}
    config["with_evolution"] = with_evolution
    if allow_source_discovery:
        config["allow_source_discovery"] = True
    config["incremental_reviews"] = incremental_reviews
    config["source_checklist_policy"] = source_checklist_policy
    if allow_timeout_correction_leads:
        config["allow_timeout_correction_leads"] = True
    if connection_reviews:
        config["connection_review_policy"] = "per-connection/1.0"
    if scientific_detail_reviews:
        config["scientific_detail_policy"] = "scientific-detail/1.0"
    if reuse_source_checklist:
        config["reuse_source_checklist"] = True
    if scope_document is not None:
        config["review_scope_sha256"] = sha(run / "review_scope.json")
    if packet is not None:
        config["prepared_sources_sha256"] = sha(run / "prepared_sources.json")
        config["prepared_registration_policy"] = "preserve-supplied-sources/1.0"
        if scope_document is not None:
            config["independent_source_renditions"] = True
    if continue_from is not None:
        config["continue_from"] = str(continue_from)
    if catalogue is not None:
        write(run / "shared_sources/index.json", catalogue)
        config["source_catalogue_sha256"] = sha(run / "shared_sources/index.json")
        config["source_runs"] = [str(Path(p).resolve()) for p in source_runs]
    if with_evolution:
        config["method_registry_sha256"] = sha(run / "method_registry.json")
    write(run / "config.json", config)
    write(run / "state.json", {"config_sha256": sha(run / "config.json"), "jobs": tasks})
    return summarize(run)

