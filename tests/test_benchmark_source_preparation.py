"""Local source-custody controls; no finder/model/network execution."""

from argparse import Namespace
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.benchmark import prepare_sources as preparation
from genomics_discovery.curation.run_state import sha, write
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet


def case(mid="assay", **updates):
    return {"case_id": mid + "_original", "method_id": mid, "family": "fixture_family",
        "split": "development", "title": "Declared benchmark method",
        "scope": "Original specimen-to-library protocol with retained QC branches",
        "variant": "Original published variant", "primary_identity": {
            "pmid": "123", "doi": "10.1234/fixture", "pmcid": "PMC123"}, **updates}


def packet(directory, declared, *, historical=False, supplement=False, pmid="123", doi="10.1234/fixture"):
    directory.mkdir(parents=True)
    primary = directory / "primary.xml"
    primary.write_text(f"""<article><front><article-meta>
      <article-id pub-id-type="pmid">{pmid}</article-id>
      <article-id pub-id-type="doi">{doi}</article-id>
      <title-group><article-title>Publication title distinct from the method label</article-title></title-group>
      </article-meta></front><body><sec><title>Methods</title>
      <p>Wash the specimen and retain an aliquot for a separate measurement.</p>
      </sec></body></article>""")
    descriptors = [{"source_id": "primary", "title": "Original primary publication",
        "role": "primary_paper", "url": "https://example.org/original.xml", "path": str(primary)}]
    if supplement:
        extra = directory / "supplement.txt"
        extra.write_text("Original supplementary protocol: retain the production fraction.")
        descriptors.append({"source_id": "supplement", "title": "Original supplement",
            "role": "supplement", "url": "https://example.org/supplement.txt", "path": str(extra)})
    options = {key: declared[key] for key in ("method_id", "title", "scope", "variant")}
    if historical:
        options.update(method_id="historical_implementation", title="Historical packet label",
                       scope="Earlier broad registration", variant="Historical source context")
    value = build_packet(**options, sources=descriptors)
    path = directory / "packet.json"
    write(path, value)
    return path


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setenv("SLURM_JOB_ID", "synthetic-allocation-no-native-execution")

    class NoNativeWorker:
        def __init__(self, **options):
            self.options = options

        async def run(self, *args, **kwargs):
            pytest.fail("Source-preparation tests must not execute a native worker")

    monkeypatch.setattr(preparation, "CodexWorker", NoNativeWorker)

    def args(cases):
        path = root / "cases.json"
        write(path, {"cases": cases})
        return Namespace(cases=path, project_root=root, output=root / "prepared", concurrency=2)

    return root, args


async def test_rescoped_packet_preserves_historical_bytes_and_source_identity(prepared, monkeypatch):
    root, arguments = prepared
    declared = case("atac-seq")
    old = packet(root / "historical", declared, historical=True, supplement=True)
    declared["existing_packet_leads"] = [str(old)]
    before = {path: path.read_bytes() for path in old.parent.iterdir()}

    async def unexpected(*args, **kwargs):
        pytest.fail("This fixture's explicitly retained source scope needs no finder")

    monkeypatch.setattr(preparation, "acquire", unexpected)
    args = arguments([declared])
    result = await preparation.prepare(args)
    selected = Path(result["cases"][0]["packet"])
    fresh = load_packet(selected)
    assert selected != old and selected.is_relative_to(args.output)
    assert {key: fresh[key] for key in ("method_id", "title", "scope", "variant")} == {
        key: declared[key] for key in ("method_id", "title", "scope", "variant")}
    assert fresh["sources"] == load_packet(old)["sources"]
    assert all(path.read_bytes() == value for path, value in before.items())
    pins = preparation.read(args.output / "input_bindings.json")
    assert pins[str(old)] == sha(old)
    assert all(pins[row["original_path"]] == row["original_sha256"] for row in fresh["sources"])
    assert result["cases"][0]["packet_sha256"] == sha(selected)
    with pytest.raises(FileExistsError):
        await preparation.prepare(args)
    assert all(path.read_bytes() == value for path, value in before.items())


async def test_finder_receives_fresh_source_scope_and_no_old_review_context(prepared, monkeypatch):
    root, arguments = prepared
    declared = case()
    old = packet(root / "historical", declared, historical=True)
    write(old.parent / "workflow.json", {"candidate_answer": "NEVER SUPPLY TO SOURCE FINDER"})
    write(old.parent / "review.json", {"review_answer": "NEVER SUPPLY TO SOURCE FINDER"})
    declared["existing_packet_leads"] = [str(old)]
    calls = []

    async def acquire(worklist, output, *, scopes, concurrency, worker):
        value = preparation.read(worklist)
        calls.append(value)
        row = value["methods"][0]
        fresh_path = Path(row["source_packet"]["path"])
        assert fresh_path != old
        assert load_packet(fresh_path)["scope"] == declared["scope"]
        assert scopes["assay"]["primary_identity"] == {"pmid": "123", "doi": "10.1234/fixture"}
        assert "review.json" not in json.dumps(value) and "workflow.json" not in json.dumps(value)
        assert "NEVER SUPPLY" not in json.dumps(value)
        assert worker.options["model"] == "gpt-6.1-sol" and worker.options["effort"] == "xhigh"
        chosen = output / "registered.json"
        write(chosen, load_packet(fresh_path))
        return {"methods": {"assay": {"status": "registered_for_fresh_source_review", "packet": str(chosen)}}}

    monkeypatch.setattr(preparation, "acquire", acquire)
    args = arguments([declared])
    result = await preparation.prepare(args)
    assert len(calls) == 1 and [row["case_id"] for row in result["cases"]] == [declared["case_id"]]
    report = preparation.read(args.output / "source_report.json")
    assert report["annotation_ready_is_not_scientific_completeness"] is True


async def test_mixed_acquisition_keeps_success_and_excludes_missing_case(prepared, monkeypatch):
    root, arguments = prepared
    good, missing = case("good"), case("missing")
    acquired = packet(root / "retrieved", good)

    async def acquire(*args, **kwargs):
        return {"methods": {"good": {"status": "registered_for_fresh_source_review", "packet": str(acquired)},
                            "missing": {"status": "failed", "packet": None, "error": "Public source unavailable"}}}

    monkeypatch.setattr(preparation, "acquire", acquire)
    args = arguments([good, missing])
    result = await preparation.prepare(args)
    assert [row["method_id"] for row in result["cases"]] == ["good"]
    report = preparation.read(args.output / "source_report.json")
    assert report["cases"]["missing"]["status"] == "source_acquisition_needed"
    assert report["cases"]["missing"]["acquisition_status"] == "failed"
    assert report["annotation_ready_is_not_scientific_completeness"] is True
    assert result["source_report"]["sha256"] == sha(args.output / "source_report.json")


async def test_failed_required_search_does_not_promote_retained_partial_packet(prepared, monkeypatch):
    root, arguments = prepared
    declared = case()
    old = packet(root / "historical", declared, historical=True)
    declared["existing_packet_leads"] = [str(old)]

    async def acquire(*args, **kwargs):
        return {"methods": {"assay": {"status": "failed", "packet": None}}}

    monkeypatch.setattr(preparation, "acquire", acquire)
    args = arguments([declared])
    result = await preparation.prepare(args)
    assert result["cases"] == []
    report = preparation.read(args.output / "source_report.json")["cases"]["assay"]
    assert report["status"] == "source_acquisition_needed"
    assert Path(report["retained_packet"]).is_file()
    assert report["previous_packet"]["sha256"] == sha(old)


@pytest.mark.parametrize("defect", ["method_id", "title", "scope", "variant", "pmid", "doi"])
async def test_final_acquisition_packet_cannot_cross_case_scope_or_publication(prepared, monkeypatch, defect):
    root, arguments = prepared
    declared = case()
    wrong = deepcopy(declared)
    if defect not in {"pmid", "doi"}:
        wrong[defect] = "another_case_or_scope"
    acquired = packet(root / "wrong", wrong,
        pmid="456" if defect == "pmid" else "123",
        doi="10.1234/another" if defect == "doi" else "10.1234/fixture")

    async def acquire(*args, **kwargs):
        return {"methods": {"assay": {"status": "registered_for_fresh_source_review", "packet": str(acquired)}}}

    monkeypatch.setattr(preparation, "acquire", acquire)
    args = arguments([declared])
    assert (await preparation.prepare(args))["cases"] == []
    report = preparation.read(args.output / "source_report.json")["cases"]["assay"]
    assert report["status"] == "source_identity_or_scope_inspection_needed"
    assert defect in report["reason"]


@pytest.mark.parametrize("defect", ["pmid", "doi"])
async def test_wrong_historical_publication_does_not_skip_new_search(prepared, monkeypatch, defect):
    root, arguments = prepared
    declared = case("atac-seq")
    old = packet(root / "wrong_history", declared, supplement=True,
        pmid="456" if defect == "pmid" else "123",
        doi="10.1234/another" if defect == "doi" else "10.1234/fixture")
    declared["existing_packet_leads"] = [str(old)]
    called = []

    async def acquire(worklist, *args, **kwargs):
        called.append(preparation.read(worklist))
        return {"methods": {"atac-seq": {"status": "failed", "packet": None}}}

    monkeypatch.setattr(preparation, "acquire", acquire)
    args = arguments([declared])
    assert (await preparation.prepare(args))["cases"] == []
    assert called and called[0]["methods"][0]["source_packet"] is None
    failures = preparation.read(args.output / "source_report.json")["cases"]["atac-seq"]["invalid_existing_leads"]
    assert any(defect in row["error"] for row in failures)


@pytest.mark.parametrize("mutated", ["cases", "historical_packet", "original_source"])
async def test_acquisition_cannot_hide_changed_inputs(prepared, monkeypatch, mutated):
    root, arguments = prepared
    declared = case()
    old = packet(root / "historical", declared, historical=True)
    declared["existing_packet_leads"] = [str(old)]
    args = arguments([declared])
    target = {"cases": args.cases, "historical_packet": old,
              "original_source": old.parent / "primary.xml"}[mutated]

    async def acquire(*args, **kwargs):
        target.write_bytes(target.read_bytes() + b"\nchanged during acquisition")
        return {"methods": {"assay": {"status": "failed", "packet": None}}}

    monkeypatch.setattr(preparation, "acquire", acquire)
    with pytest.raises(ValueError, match="input changed"):
        await preparation.prepare(args)
    assert not (args.output / "annotation_manifest.json").exists()


async def test_native_source_preparation_requires_allocation(prepared, monkeypatch):
    _, arguments = prepared
    args = arguments([case()])
    monkeypatch.delenv("SLURM_JOB_ID")
    with pytest.raises(RuntimeError, match="Slurm allocation"):
        await preparation.prepare(args)
    assert not args.output.exists()


@pytest.mark.parametrize("duplicate_key", ["case_id", "method_id"])
async def test_ambiguous_case_identity_cannot_create_annotation_work(prepared, monkeypatch, duplicate_key):
    _, arguments = prepared
    first, second = case("first"), case("second")
    second[duplicate_key] = first[duplicate_key]

    async def unexpected(*args, **kwargs):
        pytest.fail("Ambiguous case identity must be rejected before acquisition")

    monkeypatch.setattr(preparation, "acquire", unexpected)
    args = arguments([first, second])
    with pytest.raises(ValueError, match="must be unique"):
        await preparation.prepare(args)
    assert not (args.output / "annotation_manifest.json").exists()
