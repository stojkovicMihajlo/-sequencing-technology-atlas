"""Independent readers preserve packet files, not unseen sibling conversions."""

from copy import deepcopy
from pathlib import Path

import pytest
from test_curation_coverage import CoverageWorker, scoped_case
from test_curation_prepared_sources import setup_case

from genomics_discovery.curation import harness
from genomics_discovery.curation.coverage import (
    _sibling_source_identity,
    validate_checklist,
)
from genomics_discovery.curation.validation import (
    EVIDENCE_POLICY,
    validate_extraction,
    validate_review,
)


def add_view(document, directory, name, *, bound=True):
    meta = document["sources"][0]["source_metadata"]
    paths = meta["local_evidence"]
    paths = paths if isinstance(paths, list) else [paths]
    original = Path(paths[0])
    view = directory / name
    view.write_text("Synthetic converted source text; integrity fixture only.")
    meta["local_evidence"] = [*paths, str(view)]
    meta["sha256"] = {path: harness.sha(Path(path)) for path in meta["local_evidence"]}
    meta["artifact_roles"][str(view)] = "source_rendition"
    if bound:
        meta.setdefault("text_renditions", []).append({
            "path": str(view), "sha256": harness.sha(view),
            "source_path": str(original), "source_sha256": harness.sha(original),
            "parser": "synthetic independent converter", "parser_version": "1",
        })
    return view


class IndependentViewsWorker(CoverageWorker):
    def __init__(self, *, unbound_reader=False):
        super().__init__()
        self.unbound_reader = unbound_reader

    async def run(self, prompt, attempt):
        result = await super().run(prompt, attempt)
        if attempt.name.endswith("_source_checklist"):
            path = attempt / "checklist.json"
            document = harness.read(path)
            add_view(document, attempt, "reader.txt", bound=not self.unbound_reader)
        elif attempt.name.endswith("_extractor"):
            path = attempt / "extraction.json"
            document = harness.read(path)
            add_view(document, attempt, "extractor.txt")
        else:
            path = attempt / "review.json"
            document = harness.read(path)
            state = harness.read(attempt.parents[2] / "state.json")
            extraction = harness.read(Path(state["jobs"]["method-0"]["extraction"]))
            meta = extraction["sources"][0]["source_metadata"]
            document["source_checks"][0]["files"] = [
                {"path": file, "sha256": meta["sha256"][file]}
                for file in meta["local_evidence"]]
        harness.write(path, document)
        return result


@pytest.mark.asyncio
async def test_reader_conversion_is_checked_before_extraction(tmp_path):
    run, _, _, _ = scoped_case(tmp_path)
    worker = IndependentViewsWorker(unbound_reader=True)
    result = await harness.execute(run, worker=worker)
    assert result["counts"] == {"failed": 1}
    assert [call[1] for call in worker.calls] == ["source_checklist"]
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    assert "Missing additional rendition provenance coverage" in str(job["last_error"])
    assert harness.export_accepted(run)["profiles"] == 0


@pytest.mark.asyncio
async def test_different_bound_sibling_views_accept_without_exposing_checklist(tmp_path):
    run, _, _, _ = scoped_case(tmp_path)
    worker = IndependentViewsWorker()
    result = await harness.execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert [call[1] for call in worker.calls] == [
        "source_checklist", "extractor", "independent_verifier"]
    config = harness.read(run / "config.json")
    assert config["independent_source_renditions"] is True
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    assert job["source_checklist"] not in worker.calls[1][3]
    assert "CHECKLIST_ONLY_IDENTITY_REQUIREMENT" not in worker.calls[1][3]
    reader = harness.read(Path(job["source_checklist"]))["sources"][0]["source_metadata"]
    extractor = harness.read(Path(job["extraction"]))["sources"][0]["source_metadata"]
    assert reader["local_evidence"][-1] not in extractor["local_evidence"]
    assert harness.export_accepted(run)["profiles"] == 1
    assert harness.review_policy(config, job, {})["independent_source_renditions"] is True


@pytest.fixture
async def case(tmp_path):
    run, atlas, _, scope = scoped_case(tmp_path)
    await harness.execute(run, worker=CoverageWorker())
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    return {"run": run, "atlas": atlas, "scope": scope,
            "checklist": Path(job["source_checklist"]),
            "extraction": Path(job["extraction"]), "review": Path(job["review"]),
            "packet": harness.read(run / "prepared_sources.json")}


def reader_errors(case, *, packet=True):
    return validate_checklist(case["checklist"], "method-0", (case["atlas"], case["run"]),
                              case["scope"], evidence_policy=EVIDENCE_POLICY,
                              prepared_sources=case["packet"] if packet else None)


def review_errors(case, *, independent=True):
    return validate_review(case["review"], case["extraction"], "method-0",
                           (case["atlas"], case["run"]), evidence_policy=EVIDENCE_POLICY,
                           review_scopes={"method-0": case["scope"]},
                           source_checklist=case["checklist"], prepared_sources=case["packet"],
                           independent_source_renditions=independent)


def update_checklist_pin(case, document):
    harness.write(case["checklist"], document)
    verdict = harness.read(case["review"])
    verdict["source_to_record_check"]["checklist_sha256"] = harness.sha(case["checklist"])
    harness.write(case["review"], verdict)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["unbound", "wrong_parent", "wrong_hash", "no_parser",
                                     "no_version", "source_loss", "replacement", "role",
                                     "title", "url", "duplicate_id"])
async def test_reader_registration_rejects_before_review(case, mutation):
    document = harness.read(case["checklist"])
    view = add_view(document, case["checklist"].parent, "reader.txt", bound=mutation != "unbound")
    meta = document["sources"][0]["source_metadata"]
    if mutation == "wrong_parent":
        meta["text_renditions"][0]["source_path"] = str(view)
    elif mutation == "wrong_hash":
        meta["text_renditions"][0]["source_sha256"] = "0" * 64
    elif mutation in {"no_parser", "no_version"}:
        meta["text_renditions"][0].pop("parser" if mutation == "no_parser" else "parser_version")
    elif mutation in {"source_loss", "replacement"}:
        original = meta["local_evidence"].pop(0)
        meta["sha256"].pop(original)
        meta["artifact_roles"].pop(original)
        if mutation == "replacement":
            replacement = case["checklist"].parent / "replacement.txt"
            replacement.write_bytes(Path(original).read_bytes())
            meta["local_evidence"].append(str(replacement))
            meta["sha256"][str(replacement)] = harness.sha(replacement)
            meta["artifact_roles"][str(replacement)] = "source_document"
    elif mutation == "role":
        meta["artifact_roles"][meta["local_evidence"][0]] = "source_rendition"
    elif mutation in {"title", "url"}:
        meta["title" if mutation == "title" else "primary_url"] = "Different identity"
    elif mutation == "duplicate_id":
        document["sources"].append(deepcopy(document["sources"][0]))
    harness.write(case["checklist"], document)
    assert reader_errors(case)


@pytest.mark.asyncio
async def test_legacy_mode_keeps_the_old_superset_gate(case):
    document = harness.read(case["checklist"])
    add_view(document, case["checklist"].parent, "reader.txt")
    update_checklist_pin(case, document)
    assert review_errors(case) == []
    assert "omitted frozen artifacts" in str(review_errors(case, independent=False))
    # Legacy reader validation never acquires new prepared-binding requirements.
    meta = document["sources"][0]["source_metadata"]
    meta.pop("text_renditions")
    harness.write(case["checklist"], document)
    assert reader_errors(case, packet=False) == []
    assert "Missing additional rendition provenance coverage" in str(reader_errors(case))


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["reader", "extractor"])
async def test_supplied_rendition_is_preserved_without_retrofitting_provenance(case, role):
    supplied = case["atlas"] / "papers/frozen-view.txt"
    supplied.write_text("Frozen supplied conversion fixture")
    case["packet"]["methods"]["method-0"][0]["files"].append({
        "path": str(supplied), "sha256": harness.sha(supplied), "role": "source_rendition"})
    for path in (case["checklist"], case["extraction"]):
        document = harness.read(path)
        meta = document["sources"][0]["source_metadata"]
        original = meta["local_evidence"]
        original = original[0] if isinstance(original, list) else original
        meta["local_evidence"] = [original, str(supplied)]
        meta["sha256"] = {p: harness.sha(Path(p)) for p in meta["local_evidence"]}
        meta["artifact_roles"][str(supplied)] = "source_rendition"
        harness.write(path, document)
    assert reader_errors(case) == []
    assert validate_extraction(case["extraction"], "method-0", (case["atlas"], case["run"]),
                               evidence_policy=EVIDENCE_POLICY, prepared_sources=case["packet"],
                               independent_source_renditions=True) == []
    path = case["checklist"] if role == "reader" else case["extraction"]
    document = harness.read(path)
    meta = document["sources"][0]["source_metadata"]
    meta["local_evidence"].remove(str(supplied))
    meta["sha256"].pop(str(supplied))
    meta["artifact_roles"].pop(str(supplied))
    harness.write(path, document)
    errors = reader_errors(case) if role == "reader" else validate_extraction(
        path, "method-0", (case["atlas"], case["run"]), evidence_policy=EVIDENCE_POLICY,
        prepared_sources=case["packet"], independent_source_renditions=True)
    assert "omitted frozen artifacts: frozen-view.txt" in str(errors)


@pytest.mark.parametrize("mutation", ["hash", "role", "document", "replacement",
                                     "added_document", "title", "url"])
def test_sibling_comparison_preserves_shared_paths_and_original_identity(tmp_path, mutation):
    original, view = tmp_path / "original.txt", tmp_path / "shared.txt"
    files = {original: "a" * 64, view: "b" * 64}
    meta = {"title": "Synthetic original", "primary_url": "https://example.org/original",
            "artifact_roles": {str(original): "source_document", str(view): "source_rendition"}}
    changed_meta, changed_files = deepcopy(meta), dict(files)
    if mutation == "hash":
        changed_files[view] = "c" * 64
    elif mutation == "role":
        changed_meta["artifact_roles"][str(view)] = "source_document"
    elif mutation == "document":
        changed_files.pop(original)
        changed_meta["artifact_roles"].pop(str(original))
    elif mutation in {"replacement", "added_document"}:
        replacement = tmp_path / "replacement.txt"
        changed_files[replacement] = files[original]
        changed_meta["artifact_roles"][str(replacement)] = "source_document"
        if mutation == "replacement":
            changed_files.pop(original)
            changed_meta["artifact_roles"].pop(str(original))
    else:
        changed_meta["title" if mutation == "title" else "primary_url"] = "Different identity"
    with pytest.raises(ValueError, match="Checklist source identity differs"):
        _sibling_source_identity(meta, files, changed_meta, changed_files,
                                 original_base=tmp_path, current_base=tmp_path, sid="source")


@pytest.mark.asyncio
async def test_distinct_discovered_ids_and_unused_packet_leads_remain_valid(case):
    unused = deepcopy(case["packet"]["methods"]["method-0"][0])
    unused["source_id"] = "unused-discovery-lead"
    case["packet"]["methods"]["method-0"].append(unused)
    document = harness.read(case["checklist"])
    discovered = deepcopy(document["sources"][0])
    discovered["source_id"] = "discovered-only-by-reader"
    original = case["checklist"].parent / "new-discovered-original.txt"
    original.write_text("Distinct independently acquired synthetic original source.")
    discovered["source_metadata"].update(
        title="Separate source identity", primary_url="https://example.org/discovered",
        local_evidence=[str(original)], sha256={str(original): harness.sha(original)},
        artifact_roles={str(original): "source_document"})
    document["sources"].append(discovered)
    # A new source's converter still needs its own original parent and parser.
    independent = {"sources": [discovered]}
    add_view(independent, case["checklist"].parent, "discovered.txt")
    update_checklist_pin(case, document)
    assert reader_errors(case) == []
    assert review_errors(case) == []
    discovered["source_metadata"].pop("text_renditions")
    harness.write(case["checklist"], document)
    assert "Missing additional rendition provenance coverage" in str(reader_errors(case))


@pytest.mark.asyncio
async def test_new_policy_does_not_merge_colliding_reviewer_source_ids(case):
    verdict = harness.read(case["review"])
    verdict["sources"] = deepcopy(harness.read(case["extraction"])["sources"])
    harness.write(case["review"], verdict)
    assert "Reviewer source_id collides" in str(review_errors(case))


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, 0, 1, "true", []])
async def test_policy_flag_is_boolean(case, flag):
    assert "must be boolean" in str(review_errors(case, independent=flag))
    assert "must be boolean" in str(validate_extraction(
        case["extraction"], "method-0", (case["atlas"], case["run"]),
        independent_source_renditions=flag))


def test_absent_legacy_policy_is_not_silently_enabled(tmp_path):
    run, atlas, packet, _, _ = setup_case(tmp_path)
    harness.initialize(run, atlas, prepared_sources=packet)
    config = harness.read(run / "config.json")
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    assert "independent_source_renditions" not in config
    assert "independent_source_renditions" not in harness.validation_options(run, config)
    assert "independent_source_renditions" not in harness.review_policy(config, job, {})
