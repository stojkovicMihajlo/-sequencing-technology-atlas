"""Planning-owned identity must survive author schema, prompt and self-validation."""
import asyncio
import copy
import json

import pytest

from genomics_discovery.illumina import extraction as batch
from genomics_discovery.illumina.models import extraction_model, parse_extraction
from tests.illumina import test_illumina_batch_controls as transport
from tests.illumina.test_illumina_batch_controls import acquired


def with_names(document, names):
    raw = document.model_dump(mode="json")
    raw["method_names"] = list(names)
    scope = raw["method_scopes"][0]
    raw["method_scopes"] = [{**copy.deepcopy(scope), "method": name} for name in names]
    for match in raw["paper_matches"]:
        match["methods"] = list(names)
    return parse_extraction(raw)


@pytest.mark.parametrize("version", ["illumina_diagram/1.0", "illumina_diagram/1.1"])
@pytest.mark.parametrize("names", [["Smart-Seq2"], ["Method A", "Method B"]])
def test_schema_binds_exact_members_without_prescribing_order(version, names):
    model = extraction_model(version)
    original = model.model_json_schema()
    field = batch.author_schema(model, {"verified_method_names": names})["properties"]["method_names"]
    assert field["items"]["enum"] == names
    assert field["minItems"] == field["maxItems"] == len(names)
    assert field["uniqueItems"] is True
    assert model.model_json_schema() == original
    assert "enum" not in batch.author_schema(model, {})["properties"]["method_names"]["items"]


@pytest.mark.parametrize("names", [["Smart-Seq2"], ["Smart-Seq2", "Another method"]])
def test_self_check_rejects_case_changed_member_and_accepts_exact_members(acquired, tmp_path, capsys, names):
    root, card, acquired_sources, _ = acquired
    sources, _ = batch.card_sources(card, acquired_sources, root)
    card["verified_method_names"] = names
    batch.write(tmp_path / "card.json", card)
    batch.write(tmp_path / "registry.json", sources)
    command = ["check", str(tmp_path / "artifact.json"), "--registry", str(tmp_path / "registry.json"),
               "--record-id", card["record_id"], "--card", str(tmp_path / "card.json")]
    original = transport.candidate(sources)
    wrong = ["Smart-seq2", *names[1:]]
    batch.write(tmp_path / "artifact.json", with_names(original, wrong).model_dump(mode="json"))
    assert batch.main(command) == 1
    errors = json.loads(capsys.readouterr().out)
    assert errors["valid"] is False
    assert "Candidate method names differ from source-attested expected members" in errors["errors"]
    batch.write(tmp_path / "artifact.json", with_names(original, list(reversed(names))).model_dump(mode="json"))
    assert batch.main(command) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


@pytest.mark.parametrize("names", [["Smart-Seq2"], ["Method A", "Method B"]])
def test_staged_author_receives_exact_names_in_prompt_schema_and_card(acquired, tmp_path, monkeypatch, names):
    root, card, sources, _ = acquired
    card["verified_method_names"] = names
    factory = transport.candidate
    monkeypatch.setattr(transport, "candidate", lambda source, **kwargs: with_names(factory(source, **kwargs), names))
    worker = transport.FakeWorker()
    result = asyncio.run(batch.extract_card(card, sources, root, tmp_path / "method", worker))
    assert result["status"] == "supported"
    call = worker.calls[0]
    context = json.loads(call["prompt"].split("CALLER CONTEXT\n", 1)[1].split("\n\nREQUIRED OUTPUT SCHEMA", 1)[0])
    assert context["expected_method_names"] == names
    assert "preserving spelling and capitalization" in call["prompt"]
    assert "--card" in context["self_check"]
    assert batch.read(context["card"])["verified_method_names"] == names
    field = batch.read(call["attempt"] / "schema.json")["properties"]["method_names"]
    assert field["items"]["enum"] == names


def test_runtime_rejects_same_mismatch_before_review(acquired, tmp_path):
    root, card, sources, _ = acquired
    card["verified_method_names"] = ["Example Assay"]
    worker = transport.FakeWorker()
    with pytest.raises(batch.StageFailure, match="method names differ"):
        asyncio.run(batch.extract_card(card, sources, root, tmp_path / "method", worker))
    assert [call["role"] for call in worker.calls] == ["author"]
    assert batch.read(tmp_path / "method" / "stages.json")[0]["failure_category"] == "artifact_validation"
