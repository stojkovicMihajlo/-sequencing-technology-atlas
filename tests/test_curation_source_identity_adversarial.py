"""Cross-ID integrity faults use synthetic sources and no native model workers."""

from pathlib import Path

import pytest
from test_curation_coverage import CoverageWorker
from test_curation_prepared_sources import setup_case

from genomics_discovery.curation import harness
from genomics_discovery.curation.coverage import SCOPE_FORMAT
from genomics_discovery.curation.validation import (
    EVIDENCE_POLICY,
    validate_extraction,
    validate_review,
)


def _replace_source_ids(value, new_id):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_id":
                value[key] = new_id
            else:
                _replace_source_ids(child, new_id)
    elif isinstance(value, list):
        for child in value:
            _replace_source_ids(child, new_id)


def _setup(tmp_path, kind):
    _, atlas, packet_path, original, packet = setup_case(tmp_path)
    supplied_view = atlas / "papers/frozen-rendition.txt"
    supplied_view.write_text("Synthetic supplied rendition, distinct from original bytes.")
    packet["methods"]["method-0"][0]["files"].append({
        "path": str(supplied_view), "sha256": harness.sha(supplied_view),
        "role": "source_rendition"})
    harness.write(packet_path, packet)
    if kind == "same_path":
        declared_original = supplied_view
    elif kind == "copied_bytes":
        declared_original = atlas / "papers/relabelled-original.txt"
        declared_original.write_bytes(supplied_view.read_bytes())
    elif kind == "known_original_same_path":
        declared_original = original
    elif kind == "known_original_copied_bytes":
        declared_original = atlas / "papers/known-original-copy.txt"
        declared_original.write_bytes(original.read_bytes())
    else:
        declared_original = atlas / "papers/newly-acquired-original.txt"
        declared_original.write_text("An independently acquired synthetic original document.")
    scope = {
        "implementation": "Synthetic published implementation, version 1",
        "coverage": "Attributed signal architecture; synthetic integrity fixture only",
        "required_outputs": ["An attributed signal record"],
        "included_branches": ["Primary readout"], "excluded_branches": []}
    scope_path = atlas / "adversarial-scope.json"
    harness.write(scope_path, {"format_version": SCOPE_FORMAT,
                               "methods": {"method-0": scope}})
    run = atlas / "work/adversarial-source-identity"
    harness.initialize(run, atlas, source_only=True, prepared_sources=packet_path,
                       review_scope=scope_path, max_attempts=1, max_revisions=0)
    return run, atlas, original, supplied_view, declared_original


class _NewIdentityWorker(CoverageWorker):
    """A new source ID must not erase a supplied artifact's frozen role."""

    source_id = "independently-registered-source"

    def __init__(self, declared_original, *, new_conversion=False, bad_parent=False,
                 different_extractor_original=False, identity=None):
        super().__init__()
        self.declared_original = declared_original
        self.new_conversion = new_conversion
        self.bad_parent = bad_parent
        self.different_extractor_original = different_extractor_original
        self.identity = identity or {
            "title": "Independent synthetic document",
            "primary_url": "https://example.org/independent-original"}

    def _registration(self, attempt):
        original = self.declared_original
        if self.different_extractor_original and attempt.name.endswith("_extractor"):
            original = original.with_name("different-extractor-original.txt")
            original.write_text("A different original source under the same asserted ID.")
        meta = {
            **self.identity, "inspected_url": self.identity["primary_url"],
            "sections_inspected": ["Methods"], "access_limits": [],
            "local_evidence": [str(original)],
            "sha256": {str(original): harness.sha(original)},
            "artifact_roles": {str(original): "source_document"}}
        if self.new_conversion:
            view = attempt / "independent-view.txt"
            view.write_text("Synthetic independent conversion; no entailment claim.")
            meta["local_evidence"].append(str(view))
            meta["sha256"][str(view)] = harness.sha(view)
            meta["artifact_roles"][str(view)] = "source_rendition"
            parent = view if self.bad_parent else original
            meta["text_renditions"] = [{
                "path": str(view), "sha256": harness.sha(view),
                "source_path": str(parent), "source_sha256": harness.sha(parent),
                "parser": "synthetic independent converter", "parser_version": "1"}]
        return {"source_id": self.source_id, "source_metadata": meta}

    async def run(self, prompt, attempt):
        result = await super().run(prompt, attempt)
        if attempt.name.endswith("_source_checklist"):
            path = attempt / "checklist.json"
            document = harness.read(path)
            document["sources"] = [self._registration(attempt)]
        elif attempt.name.endswith("_extractor"):
            path = attempt / "extraction.json"
            document = harness.read(path)
            document["sources"] = [self._registration(attempt)]
        else:
            path = attempt / "review.json"
            document = harness.read(path)
            state = harness.read(attempt.parents[2] / "state.json")
            extraction = harness.read(Path(state["jobs"]["method-0"]["extraction"]))
            meta = extraction["sources"][0]["source_metadata"]
            document["source_checks"][0]["files"] = [
                {"path": p, "sha256": meta["sha256"][p]}
                for p in meta["local_evidence"]]
        _replace_source_ids(document, self.source_id)
        harness.write(path, document)
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["same_path", "copied_bytes"])
async def test_frozen_rendition_cannot_become_original_by_new_source_id(tmp_path, kind):
    run, _, original, supplied_view, declared_original = _setup(tmp_path, kind)
    frozen = {p: harness.sha(p) for p in (original, supplied_view)}
    worker = _NewIdentityWorker(
        declared_original,
        identity={"title": "Original document", "primary_url": "https://example.org/original"})
    result = await harness.execute(run, worker=worker)
    assert {p: harness.sha(p) for p in frozen} == frozen
    exported_profiles = harness.export_accepted(run)["profiles"]
    assert result["counts"] == {"failed": 1}, (
        "A source-ID alias promoted supplied rendition bytes to source_document: "
        f"{result['counts']}; exported_profiles={exported_profiles}")
    assert [call[1] for call in worker.calls] == ["source_checklist"]
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    assert "Prepared artifact" in str(job["last_error"])
    assert "role differs" in str(job["last_error"])
    assert exported_profiles == 0


@pytest.mark.asyncio
async def test_genuine_new_original_with_independent_bound_views_is_admissible(tmp_path):
    run, _, _, _, declared_original = _setup(tmp_path, "genuine_new_original")
    worker = _NewIdentityWorker(declared_original, new_conversion=True)
    result = await harness.execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert [call[1] for call in worker.calls] == [
        "source_checklist", "extractor", "independent_verifier"]
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    reader = harness.read(Path(job["source_checklist"]))["sources"][0]["source_metadata"]
    extractor = harness.read(Path(job["extraction"]))["sources"][0]["source_metadata"]
    assert reader["local_evidence"][-1] != extractor["local_evidence"][-1]
    assert reader["local_evidence"][-1] not in worker.calls[1][3]
    assert harness.export_accepted(run)["profiles"] == 1


@pytest.mark.asyncio
async def test_new_source_conversion_cannot_bind_itself_as_original(tmp_path):
    run, _, _, _, declared_original = _setup(tmp_path, "genuine_new_original")
    worker = _NewIdentityWorker(declared_original, new_conversion=True, bad_parent=True)
    result = await harness.execute(run, worker=worker)
    assert result["counts"] == {"failed": 1}
    assert [call[1] for call in worker.calls] == ["source_checklist"]
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    assert "frozen source_document parent" in str(job["last_error"])


@pytest.mark.asyncio
async def test_same_discovered_source_id_requires_same_sibling_originals(tmp_path):
    run, _, _, _, declared_original = _setup(tmp_path, "genuine_new_original")
    worker = _NewIdentityWorker(declared_original, different_extractor_original=True)
    result = await harness.execute(run, worker=worker)
    assert result["counts"] == {"failed": 1}
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    assert "changed original source_document registration" in str(job["last_error"])
    assert harness.export_accepted(run)["profiles"] == 0


@pytest.mark.asyncio
async def test_unused_prepared_original_is_still_byte_frozen_at_export(tmp_path):
    run, _, original, _, declared_original = _setup(tmp_path, "genuine_new_original")
    assert (await harness.execute(run, worker=_NewIdentityWorker(declared_original)))[
        "counts"] == {"accepted": 1}
    original.write_text("Changed original bytes despite uninspected source-ID lead.")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        harness.export_accepted(run)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["same_path", "copied_bytes"])
@pytest.mark.parametrize("changed_field", ["title", "primary_url"])
async def test_known_original_cannot_change_bibliography_under_new_id(
    tmp_path, kind, changed_field
):
    run, _, original, supplied_view, declared_original = _setup(
        tmp_path, "known_original_" + kind)
    identity = {"title": "Original document", "primary_url": "https://example.org/original"}
    identity[changed_field] = (
        "Different paper identity" if changed_field == "title"
        else "https://example.org/different-paper")
    frozen = {p: harness.sha(p) for p in (original, supplied_view)}
    worker = _NewIdentityWorker(declared_original, identity=identity)
    result = await harness.execute(run, worker=worker)
    assert {p: harness.sha(p) for p in frozen} == frozen
    exported_profiles = harness.export_accepted(run)["profiles"]
    assert result["counts"] == {"failed": 1}, (
        f"Known bytes were bibliographically reidentified: {result['counts']}; "
        f"exported_profiles={exported_profiles}")
    assert [call[1] for call in worker.calls] == ["source_checklist"]
    assert exported_profiles == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["same_path", "copied_bytes"])
async def test_new_id_alias_of_known_original_preserves_frozen_owner_identity(tmp_path, kind):
    run, _, _, _, declared_original = _setup(tmp_path, "known_original_" + kind)
    worker = _NewIdentityWorker(
        declared_original, new_conversion=True,
        identity={"title": "Original document", "primary_url": "https://example.org/original"})
    result = await harness.execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert harness.export_accepted(run)["profiles"] == 1


@pytest.fixture
async def direct_case(tmp_path):
    run, atlas, _, _, declared_original = _setup(tmp_path, "genuine_new_original")
    assert (await harness.execute(run, worker=_NewIdentityWorker(declared_original)))[
        "counts"] == {"accepted": 1}
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    return {
        "run": run, "atlas": atlas, "extraction": Path(job["extraction"]),
        "review": Path(job["review"]), "checklist": Path(job["source_checklist"]),
        "packet": harness.read(run / "prepared_sources.json"),
        "scopes": harness.read(run / "review_scope.json")["methods"]}


@pytest.mark.asyncio
async def test_explicit_independent_extractor_requires_frozen_packet(direct_case):
    case = direct_case
    errors = validate_extraction(
        case["extraction"], "method-0", (case["atlas"], case["run"]),
        evidence_policy=EVIDENCE_POLICY, independent_source_renditions=True)
    assert errors, "Requested independent source policy silently skipped packet binding"


@pytest.mark.asyncio
@pytest.mark.parametrize("omitted", ["prepared_sources", "review_scopes", "source_checklist"])
async def test_explicit_independent_review_requires_coverage_context(direct_case, omitted):
    case = direct_case
    options = {"prepared_sources": case["packet"], "review_scopes": case["scopes"],
               "source_checklist": case["checklist"]}
    options.pop(omitted)
    errors = validate_review(
        case["review"], case["extraction"], "method-0", (case["atlas"], case["run"]),
        evidence_policy=EVIDENCE_POLICY, independent_source_renditions=True, **options)
    assert errors, f"Requested independent review silently skipped {omitted}"


@pytest.mark.asyncio
async def test_independent_policy_rejects_packet_for_unknown_method(direct_case):
    case = direct_case
    packet = {**case["packet"], "methods": {
        "different-method": case["packet"]["methods"]["method-0"]}}
    errors = validate_extraction(
        case["extraction"], "method-0", (case["atlas"], case["run"]),
        evidence_policy=EVIDENCE_POLICY, independent_source_renditions=True,
        prepared_sources=packet)
    assert errors


@pytest.mark.asyncio
@pytest.mark.parametrize("packet_format", [None, "curation_prepared_sources/unknown"])
async def test_independent_policy_rejects_unknown_packet_format(direct_case, packet_format):
    case = direct_case
    packet = dict(case["packet"])
    if packet_format is None:
        packet.pop("format_version")
    else:
        packet["format_version"] = packet_format
    errors = validate_extraction(
        case["extraction"], "method-0", (case["atlas"], case["run"]),
        evidence_policy=EVIDENCE_POLICY, independent_source_renditions=True,
        prepared_sources=packet)
    assert errors, "Requested independent policy accepted an unknown packet contract"


@pytest.mark.asyncio
async def test_independent_review_rejects_scope_for_unknown_method(direct_case):
    case = direct_case
    errors = validate_review(
        case["review"], case["extraction"], "method-0", (case["atlas"], case["run"]),
        evidence_policy=EVIDENCE_POLICY, independent_source_renditions=True,
        prepared_sources=case["packet"], source_checklist=case["checklist"],
        review_scopes={"different-method": case["scopes"]["method-0"]})
    assert errors


@pytest.mark.asyncio
async def test_standalone_extractor_packet_binding_and_legacy_context_remain_valid(direct_case):
    case = direct_case
    roots = (case["atlas"], case["run"])
    assert validate_extraction(
        case["extraction"], "method-0", roots, evidence_policy=EVIDENCE_POLICY,
        independent_source_renditions=True, prepared_sources=case["packet"]) == []
    assert validate_extraction(
        case["extraction"], "method-0", roots, evidence_policy=EVIDENCE_POLICY) == []
    assert validate_review(
        case["review"], case["extraction"], "method-0", roots,
        evidence_policy=EVIDENCE_POLICY) == []
