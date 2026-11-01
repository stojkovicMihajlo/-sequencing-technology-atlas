"""Poster citations remain auditable candidates, including real discrepancies."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "illumina_poster_citations", Path(__file__).parents[2] / "scripts/illumina/poster_citations.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def xml(pmid="12", *, surname="Mercer", initials="TR", title="A capture protocol", year="2014",
        journal="Nature protocols", abbreviation="Nat Protoc", volume="9", page="989-1009"):
    return f'''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>
    <ArticleTitle>{title}</ArticleTitle><Journal><Title>{journal}</Title><ISOAbbreviation>{abbreviation}</ISOAbbreviation>
    <JournalIssue><Volume>{volume}</Volume><PubDate><Year>{year}</Year><Month>Apr</Month><Day>9</Day></PubDate></JournalIssue>
    </Journal><Pagination><MedlinePgn>{page}</MedlinePgn></Pagination><AuthorList><Author><LastName>{surname}</LastName>
    <Initials>{initials}</Initials></Author></AuthorList><Abstract><AbstractText>Abstract evidence only.</AbstractText></Abstract>
    </Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="pubmed">{pmid}</ArticleId></ArticleIdList>
    <ReferenceList><Reference><ArticleIdList><ArticleId IdType="pmc">PMC99</ArticleId></ArticleIdList></Reference>
    </ReferenceList></PubmedData></PubmedArticle></PubmedArticleSet>'''.encode()


def test_structured_metadata_uses_actual_author_year_and_own_identifiers():
    publication = mod.structured_pubmed(xml())["12"]
    assert publication["bibliography"]["first_author"]["surname"] == "Mercer"
    assert publication["bibliography"]["publication_years"] == [2014]
    assert publication["bibliography"]["volume"] == "9"
    assert publication["identifiers"] == {"pubmed": ["12"]}
    assert publication["abstract"][0]["text"] == "Abstract evidence only."


@pytest.mark.parametrize("raw, surname, year, journal, volume, first, last", [
    ("Mercer T. R. et al. (2014) Nat Protoc 9: 989-1009", "Mercer", 2014, "Nat Protoc", "9", "989", "1009"),
    ("Van Nostrand EL et al. (2016) Nat Methods advance online", "Van Nostrand", 2016, "Nat Methods", None, None, None),
    ("Crosetto, N. et al. Nat. Methods 10, 361–365 (2013).", "Crosetto", 2013, "Nat. Methods", "10", "361", "365"),
    ("Tang F et al. (2009) Nat Methods 6:377-82", "Tang", 2009, "Nat Methods", "6", "377", "382"),
    ("de Assis S. et al. (2012) Nat Commun 3: 1053", "de Assis", 2012, "Nat Commun", "3", "1053", None),
    ("Spies N. et al. (2013) Genome Res 23: 2078-2090", "Spies", 2013, "Genome Res", "23", "2078", "2090"),
    ("Wang Q. et al. (2013) Nat Protoc 8: 2022-2032", "Wang", 2013, "Nat Protoc", "8", "2022", "2032"),
    ("Fanelli M et al. (2011) Nat Protoc 6:1905-19", "Fanelli", 2011, "Nat Protoc", "6", "1905", "1919"),
])
def test_citation_fields_are_parsed_without_inventing_missing_parts(raw, surname, year, journal, volume, first, last):
    parsed = mod.parse_citation(raw)
    assert (parsed["first_author_surname"], parsed["year"], parsed["journal"], parsed["volume"],
            parsed["pages"]["first"], parsed["pages"]["last"]) == (surname, year, journal, volume, first, last)


def test_chapter_unit_is_not_mislabeled_as_volume_and_pagination():
    parsed = mod.parse_citation("Li H. et al. (2012) Curr Protoc Mol Biol Chapter 4: Unit 4 13")
    assert parsed["chapter_or_unit_citation"] is True
    assert parsed["journal"] == "Curr Protoc Mol Biol"
    assert parsed["volume"] is None and parsed["pages"]["first"] is None


def test_actual_poster_pagination_discrepancy_is_not_silently_repaired():
    publication = mod.structured_pubmed(xml(surname="Kwak", initials="H", year="2013", journal="Science",
                                          abbreviation="Science", volume="339", page="950-953"))["12"]
    comparison = mod.compare(mod.parse_citation("Kwak H. et al. (2013) Science 339: 950-954"), publication)
    assert comparison["field_checks"]["first_page"] is True
    assert comparison["field_checks"]["last_page"] is False
    assert comparison["all_observed_fields_agree"] is False
    assert comparison["comparison_status"] == "bibliographic_conflicts_require_review"
    assert comparison["paper_identity_verified"] is False


def test_even_complete_match_is_not_automatic_paper_or_method_identity():
    comparison = mod.compare(mod.parse_citation("Mercer T. R. et al. (2014) Nat Protoc 9:989-1009"),
                             mod.structured_pubmed(xml())["12"])
    assert comparison["all_observed_fields_agree"] is True
    assert comparison["paper_identity_verified"] is False


def test_first_author_initial_conflict_cannot_be_hidden_by_matching_surname():
    comparison = mod.compare(mod.parse_citation("Mercer X. et al. (2014) Nat Protoc 9:989-1009"),
                             mod.structured_pubmed(xml())["12"])
    assert comparison["field_checks"]["first_author_initials"] is False
    assert comparison["all_observed_fields_agree"] is False


def test_doi_comparison_preserves_significant_punctuation():
    citation = mod.parse_citation("Mercer T. R. et al. (2014) Nat Protoc 9:989-1009")
    citation["doi_as_printed_or_biorxiv_id"] = "10.1234/a-b"
    publication = mod.structured_pubmed(xml())["12"]
    publication["identifiers"]["doi"] = ["10.1234/ab"]
    assert mod.compare(citation, publication)["field_checks"]["printed_doi"] is False


def test_matcher_error_counts_never_become_fabricated_pmids():
    raw = b"Nature|2014|9|989|Mercer TR|one|AMBIGUOUS 37 citations\nScience|2013|339|950|Kwak H|two|12345\n"
    result = mod.parse_ecit(raw, ["one", "two"])
    assert result["one"]["candidate_pmids"] == []
    assert result["two"]["candidate_pmids"] == ["12345"]
    with pytest.raises(ValueError, match="requested keys"):
        mod.parse_ecit(b"Nature|2014|9|989|Mercer TR|unrequested|12345", ["one"])


def test_offline_end_to_end_preserves_citation_origin_and_catalogue_hints(tmp_path):
    raw = xml()
    batch = tmp_path / "batch.xml"
    batch.write_bytes(raw)
    source = tmp_path / "sources.json"
    source.write_text(json.dumps({"publication_batches": [{"status": "downloaded", "path": str(batch),
        "sha256": mod.sha(raw), "requested_pmids": ["12"]}], "fulltexts": {}}))
    proposals = tmp_path / "proposals.json"
    proposals.write_text(json.dumps({"rows": [{"row_id": "poster:1", "source_row_caption": "CaptureSeq",
        "source_pdf_sha256": "a" * 64, "members": [{"source_method_label": "CaptureSeq",
            "catalogue_reference_candidate_pmids": ["12"], "source_listed_citations": [{
                "citation_fragment_as_listed": "Mercer T. R. et al. (2014) Nat Protoc 9:989-1009",
                "locator": "Printed reference row"}]}]}]}))
    result = mod.run(proposals, source, tmp_path / "out")
    assert result["statistics"]["original_pubmed_records"] == 1
    assert result["statistics"]["citation_occurrences"] == 1
    assert result["resolutions"][0]["candidate_pmids"] == ["12"]
    assert result["resolutions"][0]["paper_identity_verified"] is False
    candidate = next(iter(result["citations"].values()))["pubmed_candidates"][0]
    assert any("not_identity_evidence" in origin for origin in candidate["candidate_origins"])
    assert result["publications"]["12"]["metadata_source_sha256"] == mod.sha(raw)
    assert (tmp_path / "out/poster_paper_candidates.json").is_file()


def test_bound_fulltext_rejects_tampered_text_and_leaves_historical_cache_paths_alone(tmp_path):
    path = tmp_path / "article.txt"
    path.write_text("Reading text")
    record = {"text_path": str(path), "text_sha256": mod.sha(path.read_bytes()),
              "cache_provenance": {"registration": {"path": "/old/missing/registration.json"}}}
    result = mod.bind_receipt(record, tmp_path)
    assert result["cache_provenance"] == record["cache_provenance"]
    path.write_text("Tampered text")
    with pytest.raises(ValueError, match="custody"):
        mod.bind_receipt(record, tmp_path)
