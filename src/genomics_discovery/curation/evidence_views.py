"""Exact JSON navigation with evidence text fetched separately by pointer."""

from copy import deepcopy
import re


def inspect_pointer(value, pointer):
    """Resolve an RFC 6901 JSON-string pointer; URI fragments are not accepted."""
    if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
        raise ValueError('Use an empty pointer or a slash-prefixed JSON Pointer')
    for encoded in pointer.split('/')[1:] if pointer else ():
        if re.search(r'~(?![01])', encoded):
            raise ValueError('Invalid JSON Pointer escape')
        token = encoded.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and re.fullmatch(r'0|[1-9][0-9]*', token) and int(token) < len(value):
            value = value[int(token)]
        else:
            raise ValueError('JSON Pointer does not identify an existing value')
    return deepcopy(value)


def evidence_navigation(value, pointer=''):
    """Keep non-evidence JSON verbatim; replace every evidence list by exact locators."""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            location = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
            if key == 'evidence' and isinstance(child, list):
                refs = sorted({(item['source_id'], item['passage_id']) for item in child})
                result[key] = {'pointer': location, 'count': len(child),
                    'support_types': sorted({item['support'] for item in child}),
                    'passages': [{'source_id': source, 'passage_id': passage}
                                 for source, passage in refs]}
            else:
                result[key] = evidence_navigation(child, location)
        return result
    if isinstance(value, list):
        return [evidence_navigation(child, f'{pointer}/{index}') for index, child in enumerate(value)]
    return value
