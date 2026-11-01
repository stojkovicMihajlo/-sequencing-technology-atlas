"""Fake-transport batch controls; no model calls or scientific evaluation."""

import asyncio
import hashlib
import json
from pathlib import Path
import struct
import zlib

import pytest

from genomics_discovery.illumina import extraction as batch
from genomics_discovery.execution.codex_worker import WorkerResult
from genomics_discovery.illumina.models import DiagramExtraction, DiagramReview, digest, review_targets


def png(pixel=b"\xff\xff\xff\xff"):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + pixel)) + chunk(b"IEND", b""))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def acquired(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    image = root / "diagram.png"
    image.write_bytes(png())
    card = {"record_id": "example", "source_id": "card:example", "source_title": "Example assay",
            "description_verbatim": "RNA is captured.", "source_sections": [],
            "source_page_url": "https://example.org/method", "diagram_asset_ids": ["asset:d"],
            "references": [{"pmid": "1", "role": "references",
                            "reference_id": "example:references:0"}]}
    state = {"assets": [{"asset_id": "asset:d", "source_id": "diagram:d", "status": "downloaded",
                          "path": "diagram.png", "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                          "url": "https://example.org/diagram.png"}],
             "publications": {"1": {"identity_status": "pubmed_record_retrieved", "pmid": "1",
                                      "title": "Capture study", "abstract": [
                                          {"label": "Abstract", "text": "RNA is captured."}]}},
             "fulltexts": {}}
    write(root / "sources.json", state)
    write(root / "inventory.json", {"cards": [card]})
    return root, card, state, image


def candidate(sources, *, revised=False):
    image_id = next(k for k, v in sources.items() if v["kind"] == "image")
    evidence = {"source_id": image_id, "locator": "capture arrow", "basis": "image",
                "support": "depicted", "bbox": [0, 0, 1, 1]}
    paper = {"source_id": "paper:1", "locator": "Abstract", "basis": "text",
             "support": "reported", "quote": "RNA is captured."}
    return DiagramExtraction.model_validate({
        "record_id": "example", "title": "Example assay", "method_names": ["Example assay"],
        "scope": "One source schematic", "inspected_sources": list(sources),
        "states": [{"id": "before", "label": "RNA", "kind": "material", "evidence": [evidence]},
                   {"id": "after", "label": "Captured RNA", "kind": "material", "evidence": [evidence]}],
        "operations": [{"id": "capture", "label": "Capture", "description": (
            "Corrected capture description" if revised else "Initial capture description"),
            "inputs": ["before"], "outputs": ["after"], "evidence": [evidence]}],
        "flows": [{"id": "input", "source": "before", "target": "capture",
                   "kind": "material_input", "evidence": [evidence]},
                  {"id": "output", "source": "capture", "target": "after",
                   "kind": "material_output", "evidence": [evidence]}],
        "method_scopes": [{"method": "Example assay", "operation_ids": ["capture"],
                           "status": "resolved", "rationale": "Explicit figure scope", "evidence": [evidence]}],
        "paper_matches": [{"id": "match", "source_id": "paper:1", "methods": ["Example assay"],
                           "assessment": "content_checked", "relationship": "supports_architecture",
                           "evidence": [paper]}], "corrections": [], "unknowns": [],
    })


class FakeWorker:
    """Produce auditable archive shape; inspect actual staged image arguments."""

    def __init__(self, *, request_correction=False, mutate_original=False, forged_prompt=False,
                 altered_review_input=False, missing_native_image=False):
        self.calls = []
        self.request_correction = request_correction
        self.mutate_original = mutate_original
        self.forged_prompt = forged_prompt
        self.altered_review_input = altered_review_input
        self.missing_native_image = missing_native_image

    async def run(self, prompt, attempt_dir, *, image_paths=()):
        attempt = Path(attempt_dir)
        source_path = attempt.parent.parent / "source_registry.json"
        sources = batch.read(source_path)
        role = "author" if attempt.name.endswith("author") else "reviewer"
        assert image_paths, "No actual source image was attached"
        for image in image_paths:
            assert Path(image).is_relative_to(attempt)
            assert Path(image).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        self.calls.append({"role": role, "attempt": attempt, "prompt": prompt,
                           "images": list(image_paths)})
        if role == "author":
            authors = sum(x["role"] == "author" for x in self.calls)
            if authors > 1 and self.request_correction:
                assert "PREVIOUS CANDIDATE" in prompt and "PREVIOUS REVIEW" in prompt
                assert "Initial capture description" in prompt
                assert "Source-backed repair requested" in prompt
            document = candidate(sources, revised=authors > 1)
        else:
            current = DiagramExtraction.model_validate_json((attempt / "candidate.json").read_bytes())
            image = current.states[0].evidence
            checks = []
            first_review = sum(x["role"] == "reviewer" for x in self.calls) == 1
            for target in sorted(review_targets(current)):
                evidence = current.paper_matches[0].evidence if target == "paper:match" else image
                negative = self.request_correction and first_review and target == "operation:capture"
                checks.append({"target": target, "verdict": "unsupported" if negative else "supported",
                               "action": "revise" if negative else "accept",
                               "rationale": "Source-backed repair requested" if negative else "Source checked",
                               "evidence": evidence})
            document = DiagramReview(record_id=current.record_id, candidate_sha256=digest(current),
                                     inspected_sources=current.inspected_sources, checks=checks)
        write(attempt / "artifact.json", document.model_dump(mode="json"))
        thread = "fake-thread-" + str(len(self.calls))
        usage = {"input_tokens": 10, "output_tokens": 5}
        result = WorkerResult(0, thread, usage, 0.1, None)
        (attempt / "prompt.txt").write_text("forged" if self.forged_prompt else prompt)
        argv = ["fake-worker", "exec"]
        for image in image_paths:
            argv.extend(["--image", str(image)])
        write(attempt / "command.json", {"argv": [*argv, "-"], "cwd": str(attempt), "image_inputs": [
            {"path": str(p), "sha256": batch.sha(p)} for p in image_paths]})
        if self.missing_native_image:
            command = batch.read(attempt / "command.json")
            command["image_inputs"] = []
            write(attempt / "command.json", command)
        if self.altered_review_input and role == "reviewer":
            changed = batch.read(attempt / "candidate.json")
            changed["scope"] = "A substituted review input"
            write(attempt / "candidate.json", changed)
        write(attempt / "started.json", {"spawned": True, "hostname": "unit-test"})
        events = [{"type": "thread.started", "thread_id": thread}, {"type": "turn.started"},
                  {"type": "turn.completed", "usage": usage}]
        (attempt / "events.jsonl").write_text("".join(json.dumps(x) + "\n" for x in events))
        (attempt / "final.txt").write_text("Fake transport completed; no model call.")
        write(attempt / "result.json", {"exit_code": 0, "thread_id": thread, "usage": usage,
                                        "elapsed_seconds": 0.1, "error": None})
        if self.mutate_original and len(self.calls) == 1:
            original = next(v["path"] for v in sources.values() if v["kind"] == "image")
            Path(original).write_bytes(png(b"\xff\x00\x00\xff"))
        return result


def test_author_and_fresh_reviewer_both_receive_images(acquired, tmp_path):
    root, card, sources, _ = acquired
    worker = FakeWorker()
    result = asyncio.run(batch.extract_card(card, sources, root, tmp_path / "method", worker))
    assert result["status"] == "supported"
    assert [c["role"] for c in worker.calls] == ["author", "reviewer"]
    assert worker.calls[0]["images"] != worker.calls[1]["images"]
    graph = batch.read(tmp_path / "method" / "graph.json")
    assert graph["status"] == "supported"


def test_one_correction_preserves_prior_candidate_and_reviews_fresh(acquired, tmp_path):
    root, card, sources, _ = acquired
    worker = FakeWorker(request_correction=True)
    result = asyncio.run(batch.extract_card(card, sources, root, tmp_path / "method", worker))
    assert result["status"] == "supported" and result["revision"] == 1
    assert [c["role"] for c in worker.calls] == ["author", "reviewer", "author", "reviewer"]
    first = batch.read(tmp_path / "method" / "attempts" / "001_author" / "artifact.json")
    final = batch.read(tmp_path / "method" / "extraction.json")
    assert first["operations"][0]["description"] == "Initial capture description"
    assert final["operations"][0]["description"] == "Corrected capture description"


@pytest.mark.parametrize("fault", ["mutate_original", "forged_prompt", "altered_review_input", "missing_native_image"])
def test_source_or_archived_prompt_change_cannot_be_supported(acquired, tmp_path, fault):
    root, card, sources, _ = acquired
    worker = FakeWorker(**{fault: True})
    try:
        result = asyncio.run(batch.extract_card(card, sources, root, tmp_path / "method", worker))
    except ValueError:
        return
    assert result["status"] != "supported", "Changed source/prompt was silently accepted"


def test_resume_rejects_changed_inventory(acquired, tmp_path, monkeypatch):
    root, _, _, _ = acquired
    worker = FakeWorker()
    monkeypatch.setenv("SLURM_JOB_ID", "fake-test-allocation")
    monkeypatch.setattr(batch, "CodexWorker", lambda **kwargs: worker)
    asyncio.run(batch.run(root / "sources.json", tmp_path / "batch", workers=1))
    inventory = batch.read(root / "inventory.json")
    inventory["cards"][0]["description_verbatim"] = "A different implementation"
    write(root / "inventory.json", inventory)
    with pytest.raises(ValueError):
        asyncio.run(batch.run(root / "sources.json", tmp_path / "batch", workers=1, resume=True))


def test_resume_cannot_silently_keep_a_tampered_supported_artifact(acquired, tmp_path, monkeypatch):
    root, _, _, _ = acquired
    worker = FakeWorker()
    monkeypatch.setenv("SLURM_JOB_ID", "fake-test-allocation")
    monkeypatch.setattr(batch, "CodexWorker", lambda **kwargs: worker)
    completed = asyncio.run(batch.run(root / "sources.json", tmp_path / "batch", workers=1))
    assert completed["cards"]["example"]["status"] == "supported"
    extraction = Path(completed["cards"]["example"]["attempt_directory"]) / "extraction.json"
    value = batch.read(extraction)
    value["title"] = "Tampered output"
    write(extraction, value)
    calls = len(worker.calls)
    try:
        resumed = asyncio.run(batch.run(root / "sources.json", tmp_path / "batch", workers=1, resume=True))
    except ValueError:
        return
    assert (resumed["cards"]["example"]["status"] != "supported"
            or len(worker.calls) > calls), "Resume silently trusted changed accepted artifact bytes"


def test_registered_fulltext_without_pmcid_retains_its_actual_url(acquired):
    root, card, sources, _ = acquired
    text = root / "author-copy.txt"
    text.write_text("Capture study\nRNA is captured.")
    sources["fulltexts"]["1"] = {"status": "fulltext_acquired", "text_path": text.name,
        "text_sha256": batch.sha(text), "url": "https://university.example/capture.pdf"}
    registry, _ = batch.card_sources(card, sources, root)
    assert registry["paper:1"]["content_level"] == "fulltext"
    assert registry["paper:1"]["url"] == "https://university.example/capture.pdf"


def test_non_pubmed_primary_is_available_as_pinned_content(acquired):
    root, card, sources, _ = acquired
    reference = {"reference_id": "example:references:doi", "role": "references",
                 "source_reference": {"text": "DOI study"}}
    card["references"].append(reference)
    text = root / "doi-paper.txt"
    text.write_text("DOI study\nA distinct capture mechanism is reported.")
    sources["external_publications"] = {reference["reference_id"]: {
        "content_level": "fulltext", "identity_status": "cached_registration_identity_and_custody_verified",
        "title": "DOI study", "doi": "10.1000/example", "text_path": text.name,
        "text_sha256": batch.sha(text)}}
    registry, _ = batch.card_sources(card, sources, root)
    recovered = registry["reference:" + reference["reference_id"]]
    assert recovered["content_level"] == "fulltext"
    assert recovered["text"] == text.read_text()
    assert recovered["url"] == "https://doi.org/10.1000/example"
    text.write_text("A changed rendition")
    with pytest.raises(ValueError, match="rendition changed"):
        batch.card_sources(card, sources, root)


def test_caption_recovery_preserves_empty_catalogue_title(acquired):
    root, card, sources, _ = acquired
    card["source_title"] = None
    card["recovered_title"] = "Recovered caption"
    card["recovered_references"] = [{"reference_id": "example:recovered:1", "pmid": "2"}]
    registry, _ = batch.card_sources(card, sources, root)
    assert batch.card_title(card) == "Recovered caption"
    assert card["source_title"] is None
    assert registry[card["source_id"]]["title"] is None
    assert registry["metadata:2"]["identity_status"] == "unverified_catalogue_reference"
    assert "example:recovered:1" in " ".join(batch.paper_coverage(candidate(registry), card))


def test_candidate_paper_is_available_without_becoming_a_listed_match(acquired):
    root, card, sources, _ = acquired
    card["references"] = []
    card["require_paper_matches"] = True
    card["candidate_publications"] = [{"pmid": "1", "association_status": "unverified_candidate"}]
    registry, _ = batch.card_sources(card, sources, root)
    assert registry["paper:1"]["content_level"] == "abstract"
    assert "not_a_verified_paper_association" in registry["paper:1"]["association_status"]
    assert card["references"] == []
    assert batch.requires_paper_matches(card)


def test_derived_diagram_retains_and_rechecks_original_custody(acquired):
    root, card, sources, _ = acquired
    original = root / "poster.pdf"
    original.write_bytes(b"%PDF-standin-for-source-custody-test")
    sources["assets"][0]["original"] = {"path": original.name, "sha256": batch.sha(original)}
    registry, _ = batch.card_sources(card, sources, root)
    assert any(row["path"] == str(original.resolve()) for row in registry["diagram:d"]["custody_files"])
    original.write_bytes(b"A changed source PDF")
    with pytest.raises(ValueError, match="source original/rendition changed"):
        batch.verify_registry_files(registry)
