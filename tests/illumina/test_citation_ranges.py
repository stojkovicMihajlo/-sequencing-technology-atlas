"""Citation-location regression controls, not tests of historical entailment."""
import pytest

from genomics_discovery.illumina.citations import bibliography


def article(paragraph, labels=(7, 8, 9, 10, 11)):
    refs = ''.join(f'<ref id="ref_{n}"><label>{n}</label><mixed-citation>Paper {n}</mixed-citation></ref>'
                   for n in labels)
    return f'<article><body><p>{paragraph}</p></body><back><ref-list>{refs}</ref-list></back></article>'


def link(n, kind='bibr'):
    return f'<xref ref-type="{kind}" rid="ref_{n}">{n}</xref>'


@pytest.mark.parametrize('separator', ['-', '–', '—', ' – '])
def test_numeric_range_locates_unlinked_middle_references(separator):
    refs = bibliography(article(f'Prior methods [{link(7)}{separator}{link(11)}].'))
    assert refs['ref_8']['contexts'] == refs['ref_7']['contexts'] == refs['ref_11']['contexts']
    evidence = refs['ref_8']['context_provenance'][0]
    assert evidence['basis'] == 'printed_numeric_range'
    assert evidence['endpoint_reference_ids'] == ['ref_7', 'ref_11']
    assert refs['ref_7']['context_provenance'][0]['basis'] == 'explicit_xref'


def test_inline_markup_does_not_destroy_range_or_context():
    refs = bibliography(article(f'Prior <sup>{link(7)}</sup>–<sup>{link(11)}</sup> methods.'))
    assert refs['ref_9']['contexts'] == ['Prior 7–11 methods.']


@pytest.mark.parametrize('wrapper', [
    '<list><list-item>{first}</list-item><list-item>{second}</list-item></list>',
    '<disp-quote>{first}{second}</disp-quote>',
    '<unknown-block>{first}{second}</unknown-block>',
])
def test_range_cannot_cross_nested_structural_boundaries(wrapper):
    # JATS paragraphs may contain lists/quotes with their own paragraphs.
    # Flattening the outer paragraph used to invent the printed range 7–11.
    content = wrapper.format(first=f'<p>{link(7)}</p>', second=f'<p>–{link(11)}</p>')
    refs = bibliography(article(content))
    assert not refs['ref_8']['contexts']
    assert refs['ref_7']['contexts'] and refs['ref_11']['contexts']
    assert all(p['basis'] == 'explicit_xref' for p in refs['ref_7']['context_provenance'])


@pytest.mark.parametrize('opaque', ['<inline-graphic/>', '<inline-formula/>', '<unknown/>'])
def test_unrendered_content_cannot_silently_join_range_endpoints(opaque):
    refs = bibliography(article(f'{link(7)}{opaque}–{link(11)}'))
    assert not refs['ref_8']['contexts']
    assert refs['ref_7']['contexts'] and refs['ref_11']['contexts']


def test_range_inside_one_nested_paragraph_is_retained():
    refs = bibliography(article(f'<disp-quote><p>{link(7)}–{link(11)}</p></disp-quote>'))
    assert refs['ref_8']['contexts']
    assert all(p['basis'] == 'printed_numeric_range' for p in refs['ref_8']['context_provenance'])


@pytest.mark.parametrize('separator', [',', ';', ' and ', ' were compared with ', '–see–'])
def test_non_range_does_not_add_uncited_references(separator):
    refs = bibliography(article(f'{link(7)}{separator}{link(11)}'))
    assert refs['ref_7']['contexts']
    assert not refs['ref_8']['contexts']


@pytest.mark.parametrize('labels', [(7, 8, 10, 11), (7, 8, 9, 10, 10, 11)])
def test_missing_or_ambiguous_labels_cannot_be_guessed(labels):
    raw = article(f'{link(7)}–{link(11)}', labels)
    if len(labels) != len(set(labels)):
        raw = raw.replace('<ref id="ref_10">', '<ref id="duplicate_10">', 1)
    refs = bibliography(raw)
    assert not refs['ref_8']['contexts']


def test_number_in_id_is_not_a_bibliography_label():
    raw = article(f'{link(7)}–{link(11)}').replace('<label>9</label>', '')
    assert not bibliography(raw)['ref_8']['contexts']


def test_range_cannot_cross_a_figure_reference():
    assert not bibliography(article(f'{link(7)}–{link(9, "fig")}–{link(11)}'))['ref_8']['contexts']


def test_printed_numbers_must_agree_with_linked_reference_labels():
    raw = article(f'{link(7)}–{link(11)}').replace('rid="ref_7">7', 'rid="ref_8">7')
    refs = bibliography(raw)
    assert refs['ref_8']['contexts']  # Explicit rid remains explicit.
    assert not refs['ref_9']['contexts']


def test_multireference_xref_is_retained_without_guessing_range():
    refs = bibliography(article('<xref ref-type="bibr" rid="ref_7 ref_11">7,11</xref>'))
    assert refs['ref_7']['contexts'] == refs['ref_11']['contexts']
    assert not refs['ref_8']['contexts']


def test_namespaced_article_is_supported():
    raw = article(f'{link(7)}–{link(11)}').replace('<article>', '<article xmlns="urn:jats">')
    assert bibliography(raw)['ref_8']['contexts'] == ['7–11']


def test_duplicate_ids_cannot_overwrite_a_reference():
    with pytest.raises(ValueError, match='Duplicate bibliography'):
        bibliography(article(link(7), (7, 7)))
