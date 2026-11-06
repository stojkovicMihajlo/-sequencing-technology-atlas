"""Resolve documented category/subclassification placements for source folders."""
from pathlib import PurePath


SUBCLASS_NAMES = {
    'RNA Low-level Detection': 'RNA Low-Level Detection',
    'RNA Low-Level Detection': 'RNA Low-Level Detection',
    'DNA-Protein Interaction': 'DNA-Protein Interactions',
    'DNA-Protein Interactions': 'DNA-Protein Interactions',
    'Protein-Protein Interaction': 'Protein-Protein Interactions',
    'Protein-Protein Interactions': 'Protein-Protein Interactions',
    'DNA Rearrangements and Markers': 'Rearrangements and Markers',
}
DOMAINS = {'Genome', 'Transcriptome', 'Epigenome'}


def safe_segment(value):
    if not value or value in {'.', '..'} or '/' in value or '\\' in value or '\x00' in value or PurePath(value).is_absolute():
        raise ValueError('Invalid classification directory segment: ' + repr(value))
    return value


def placements(method, assignments):
    result = []
    for source in method['classifications']:
        path = source.get('path', [])
        if len(path) >= 2 and path[0] in DOMAINS:
            result.append({'category': path[0], 'subclassification': SUBCLASS_NAMES.get(path[1], path[1]),
                'basis': 'Original Illumina website placement', 'source': source,
                'classification_origin': 'original_illumina'})
    for a in assignments:
        if a['category'] not in DOMAINS or not a.get('basis') or not a.get('subclassification'):
            raise ValueError('Curated placement requires category, subclassification and basis: ' + method['id'])
        result.append({**a, 'subclassification': SUBCLASS_NAMES.get(a['subclassification'], a['subclassification'])})
    if not result:
        if method['entry_type'] == 'named_method':
            raise ValueError('Named method needs a documented subclassification: ' + method['id'])
        return []
    unique = {}
    for p in result:
        key = safe_segment(p['category']), safe_segment(p['subclassification'])
        if key not in unique:
            unique[key] = {**p, 'supporting_placements': []}
        unique[key]['supporting_placements'].append(p)
    return list(unique.values())


def method_directory(method, resolved):
    if method['entry_type'] != 'named_method':
        return PurePath('resources', method['entry_type'], method['id'])
    # The first original catalogue placement is an administrative storage choice.
    # Every other placement resolves to this same folder through a relative link.
    first = resolved[0]
    return PurePath('methods', first['category'], first['subclassification'], method['id'])
