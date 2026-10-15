"""Versioned source rendition is structural/provenance preservation, not semantics."""

import copy
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from genomics_discovery.curation.workflow_extraction.jats_text import RENDITION_VERSION
from genomics_discovery.curation.workflow_extraction.models import validate_evidence
from genomics_discovery.curation.workflow_extraction.sources import (
    LEGACY_FORMAT,
    build_packet,
    load_packet,
    read_passage,
    validate_packet,
)


def packet_for(tmp_path, body, **kwargs):
    source = tmp_path / "source.xml"
    source.write_text('<article xmlns:mml="http://www.w3.org/1998/Math/MathML" '
                      'xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>'
                      '<title-group><article-title>Paper</article-title></title-group>'
                      '</article-meta></front><body><sec id="methods"><title>Methods</title>'
                      + body + '</sec></body></article>', encoding="utf-8")
    return build_packet(method_id="example", title="Example", scope="Published procedure",
                        variant="one", sources=[{"source_id": "original", "title": "Paper",
                        "url": "https://example.org/paper", "role": "primary_paper",
                        "path": str(source)}], **kwargs)


def test_new_scripts_are_distinct_from_subtraction_and_preserve_chemistry(tmp_path):
    packet = packet_for(tmp_path, '<p>Normalize to 10<sup>6</sup>; subtract 10-5; '
                        'set 10<sup>-5</sup>; H<sub>2</sub>O and CaCl<sub>2</sub>.</p>')
    assert packet["format_version"] == "workflow_sources/1.1"
    assert packet["rendition_version"] == RENDITION_VERSION
    text = packet["sources"][0]["passages"][0]["text"]
    assert text == 'Normalize to 10^(6); subtract 10-5; set 10^(-5); H_(2)O and CaCl_(2).'


def test_standalone_mathml_retains_fraction_layout_without_division_inference(tmp_path):
    packet = packet_for(tmp_path, '<p>Use the following display.</p>'
                        '<disp-formula id="F1"><label>(1)</label><mml:math>'
                        '<mml:mrow><mml:mo>(</mml:mo><mml:mfrac linethickness="0">'
                        '<mml:mn>3</mml:mn><mml:mn>4</mml:mn></mml:mfrac>'
                        '<mml:mo>)</mml:mo></mml:mrow></mml:math></disp-formula>')
    passages = packet["sources"][0]["passages"]
    assert len(passages) == 2 and 'disp-formula1 [F1]' in passages[1]["locator"]
    formula = passages[1]["text"]
    assert '[MathML source, unrendered]' in formula and '3/4' not in formula
    raw = formula.split('[MathML source, unrendered] ', 1)[1]
    restored = ET.fromstring(raw)
    fraction = restored.find('.//{http://www.w3.org/1998/Math/MathML}mfrac')
    assert fraction.attrib == {"linethickness": "0"}
    assert [child.text for child in fraction] == ["3", "4"]
    assert validate_evidence({"source_id": "original", "passage_id": "p00002",
                              "quote": 'linethickness="0"'}, packet) == []


def test_one_alternative_is_selected_and_semantics_annotations_are_not_flattened(tmp_path):
    packet = packet_for(tmp_path, '<disp-formula><alternatives>'
                        '<graphic xlink:href="equation.png"/>'
                        '<mml:math><mml:semantics><mml:mrow><mml:mi>x</mml:mi>'
                        '<mml:mo>=</mml:mo><mml:mn>7</mml:mn></mml:mrow>'
                        '<mml:annotation encoding="application/x-tex">x=7</mml:annotation>'
                        '</mml:semantics></mml:math>'
                        '<tex-math>x=7 alternate</tex-math></alternatives></disp-formula>')
    formula = packet["sources"][0]["passages"][0]["text"]
    assert 'x=7 alternate' not in formula and 'equation.png' not in formula
    restored = ET.fromstring(formula.split('[MathML source, unrendered] ', 1)[1])
    assert restored.find('.//{http://www.w3.org/1998/Math/MathML}annotation').text == 'x=7'


def test_isolated_mathml_script_has_only_a_labelled_structural_preview(tmp_path):
    packet = packet_for(tmp_path, '<disp-formula><mml:math><mml:msup>'
                        '<mml:mn>10</mml:mn><mml:mn>-5</mml:mn>'
                        '</mml:msup></mml:math></disp-formula>')
    formula = packet["sources"][0]["passages"][0]
    assert formula["text"] == '[MathML script layout: (10)^(-5)]'
    assert any('not semantics' in note for note in formula["rendition_notes"])


def test_tex_is_retained_and_image_only_math_is_explicitly_unreadable(tmp_path):
    packet = packet_for(tmp_path, '<disp-formula><tex-math>z = \\sqrt{x}</tex-math>'
                        '</disp-formula><disp-formula id="image">'
                        '<graphic xlink:href="equation-only.png"/>'
                        '<alt-text>Author caption</alt-text>'
                        '</disp-formula>')
    passages = packet["sources"][0]["passages"]
    assert passages[0]["text"] == '[TeX source, unrendered] z = \\sqrt{x}'
    assert 'Formula image unreadable as text' in passages[1]["text"]
    assert 'equation-only.png' in passages[1]["text"] and 'Author caption' in passages[1]["text"]
    assert any('no image interpretation' in note for note in passages[1]["rendition_notes"])


def test_tables_and_captions_keep_cell_boundaries_and_scripts(tmp_path):
    packet = packet_for(tmp_path, '<table-wrap><table><tr><td>H<sub>2</sub>O</td>'
                        '<td>10<sup>6</sup></td></tr></table></table-wrap>'
                        '<fig><label>Figure 1</label><caption><title>Branch</title>'
                        '<p>Use CaCl<sub>2</sub>.</p></caption></fig>')
    texts = [p["text"] for p in packet["sources"][0]["passages"]]
    assert texts == ['H_(2)O | 10^(6)', 'Figure 1 ; Branch ; Use CaCl_(2).']


def test_explicit_legacy_builder_keeps_old_rendition_and_schema_exact(tmp_path):
    packet = packet_for(tmp_path, '<p>Use 10<sup>6</sup> and H<sub>2</sub>O.</p>'
                        '<disp-formula><tex-math>z=8</tex-math></disp-formula>',
                        format_version=LEGACY_FORMAT)
    assert packet["format_version"] == LEGACY_FORMAT and 'rendition_version' not in packet
    assert packet["sources"][0]["passages"] == [{
        'passage_id': 'p00001', 'locator': 'methods/p1 — Methods; normalized chars 0:16',
        'text': 'Use 106 and H2O.'}]
    validate_packet(packet)


def test_existing_frozen_packet_loads_without_rewriting_or_digest_changes():
    import genomics_discovery

    workspace = Path(genomics_discovery.__file__).resolve().parents[3]
    frozen = workspace / ('technology_atlas/v2/work/harness_development_20261014/'
                          'operation_workflow_system/4sudrb_packet.json')
    if not frozen.exists():
        pytest.skip('Local native-trial cache not included in portable checkout')
    before = frozen.read_bytes()
    packet = load_packet(frozen)
    assert packet['format_version'] == LEGACY_FORMAT and 'rendition_version' not in packet
    assert frozen.read_bytes() == before
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == hashlib.sha256(before).hexdigest()
    assert read_passage(packet, '4sudrb2014', 'p00032')['text'].endswith('normalized to be 106.')


@pytest.mark.parametrize('mutation', ['rendition_version', 'text', 'notes', 'original'])
def test_packet_mutation_is_rejected_under_its_declared_rendition_version(tmp_path, mutation):
    packet = packet_for(tmp_path, '<p>Use 10<sup>6</sup>.</p>')
    forged = copy.deepcopy(packet)
    if mutation == 'rendition_version':
        forged['rendition_version'] = 'unknown/1.0'
    elif mutation == 'text':
        forged['sources'][0]['passages'][0]['text'] = 'Use 106.'
    elif mutation == 'notes':
        forged['sources'][0]['passages'][0]['rendition_notes'] = ['Invented completeness']
    else:
        Path(forged['sources'][0]['original_path']).write_text('Changed source bytes')
    with pytest.raises(ValueError):
        validate_packet(forged)
    saved = tmp_path / 'forged.json'
    saved.write_text(json.dumps(forged))
    with pytest.raises(ValueError):
        load_packet(saved)


@pytest.mark.parametrize('xml, old_text, new_text', [
    ('<disp-formula><label>(1)</label>x+y</disp-formula>', '(1)', '(1) x+y'),
    ('<disp-formula><label>(2)</label>x<sup>2</sup>+y</disp-formula>',
     '(2) ^(2)+y', '(2) x^(2)+y'),
    ('<table-wrap><table><tr><td><disp-formula><label>(1)</label>x+y</disp-formula>'
     '</td><td>z</td></tr></table></table-wrap>', '(1) | z', '(1) x+y | z'),
    ('<fig><caption><p>Use <inline-formula><label>(1)</label>x+y</inline-formula>.'
     '</p></caption></fig>', 'Use (1) .', 'Use (1) x+y.'),
])
def test_label_tail_fix_is_versioned_in_all_enclosing_contexts(xml, old_text, new_text):
    from genomics_discovery.curation.workflow_extraction.jats_text import (
        LEGACY_RENDITION_VERSION,
        render_block,
    )

    assert render_block(ET.fromstring(xml), LEGACY_RENDITION_VERSION)[0] == old_text
    assert render_block(ET.fromstring(xml), RENDITION_VERSION)[0] == new_text


def test_direct_formula_components_and_caption_are_preserved_in_source_order(tmp_path):
    packet = packet_for(tmp_path, '<disp-formula><label>(3)</label>x + <mml:math>'
                        '<mml:msup><mml:mi>y</mml:mi><mml:mn>2</mml:mn></mml:msup>'
                        '</mml:math> = z</disp-formula><disp-formula><label>(4)</label>'
                        '<caption><p>x is the input</p></caption>'
                        '<graphic xlink:href="formula.png"/></disp-formula>')
    texts = [p['text'] for p in packet['sources'][0]['passages']]
    assert texts[0] == '(3) x + [MathML script layout: (y)^(2)] = z'
    assert texts[1] == ('(4) [Source caption: x is the input] '
                        '[Formula image unreadable as text; resources: formula.png]')


@pytest.mark.parametrize('math', [
    '<mml:math>prefix<mml:msup><mml:mi>x</mml:mi><mml:mn>2</mml:mn>'
    '</mml:msup>suffix</mml:math>',
    '<mml:math mathvariant="bold"><mml:msup><mml:mi>x</mml:mi><mml:mn>2</mml:mn>'
    '</mml:msup></mml:math>',
    '<mml:math><mml:msup><mml:mtext>not a variable</mml:mtext><mml:mn>2</mml:mn>'
    '</mml:msup></mml:math>',
    '<mml:math><mml:msup><mml:mi>a  b</mml:mi><mml:mn>2</mml:mn>'
    '</mml:msup></mml:math>',
])
def test_script_preview_falls_back_without_losing_mathml_content(tmp_path, math):
    packet = packet_for(tmp_path, '<disp-formula>' + math + '</disp-formula>')
    text = packet['sources'][0]['passages'][0]['text']
    assert '[MathML script layout:' not in text
    restored = ET.fromstring(text.split('[MathML source, unrendered] ', 1)[1])
    original = ET.fromstring('<root xmlns:mml="http://www.w3.org/1998/Math/MathML">'
                             + math + '</root>')[0]
    assert [(e.tag, e.attrib, e.text, e.tail) for e in restored.iter()] == [
        (e.tag, e.attrib, e.text, e.tail) for e in original.iter()]


def test_foreign_math_namespace_is_not_described_as_mathml(tmp_path):
    packet = packet_for(tmp_path, '<disp-formula xmlns:f="urn:foreign"><f:math>'
                        '<f:msup><f:mi>x</f:mi><f:mn>2</f:mn></f:msup></f:math>'
                        '</disp-formula>')
    text = packet['sources'][0]['passages'][0]['text']
    assert 'MathML script layout' not in text
    restored = ET.fromstring(text.split('[XML math source, unrendered] ', 1)[1])
    assert restored.tag == '{urn:foreign}math'


def test_mathml_literal_whitespace_roundtrips_through_normalized_passage(tmp_path):
    packet = packet_for(tmp_path, '<disp-formula><mml:math><mml:ms xml:space="preserve">'
                        'a  b\t c\n d</mml:ms></mml:math></disp-formula>')
    text = packet['sources'][0]['passages'][0]['text']
    restored = ET.fromstring(text.split('[MathML source, unrendered] ', 1)[1])
    assert restored[0].text == 'a  b\t c\n d'
    assert restored[0].attrib == {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'}


def test_frozen_structured_rendition_keeps_declared_version_and_cannot_be_relabeled(tmp_path):
    from genomics_discovery.curation.workflow_extraction.jats_text import LEGACY_RENDITION_VERSION

    body = '<disp-formula><label>(1)</label>x+y</disp-formula>'
    old = packet_for(tmp_path, body, rendition_version=LEGACY_RENDITION_VERSION)
    before = json.dumps(old, sort_keys=True)
    validate_packet(old)
    assert json.dumps(old, sort_keys=True) == before
    assert old['sources'][0]['passages'][0]['text'] == '(1)'
    current = packet_for(tmp_path, body)
    assert current['sources'][0]['passages'][0]['text'] == '(1) x+y'
    assert current['rendition_version'] != old['rendition_version']
    old['rendition_version'] = current['rendition_version']
    with pytest.raises(ValueError, match='Stored rendition'):
        validate_packet(old)


def test_current_frozen_structured_packet_remains_byte_identical():
    import genomics_discovery

    workspace = Path(genomics_discovery.__file__).resolve().parents[3]
    frozen = workspace / ('technology_atlas/v2/work/harness_development_20261014/'
                          'operation_workflow_system/4sudrb_packet_v11.json')
    if not frozen.exists():
        pytest.skip('Local native-trial cache not included in portable checkout')
    before = frozen.read_bytes()
    packet = load_packet(frozen)
    assert packet['rendition_version'] == 'structured_jats_text/1.0'
    assert frozen.read_bytes() == before
