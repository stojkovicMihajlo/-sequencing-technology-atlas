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


def bounded_json_value(value, pointer='', *, start=0, limit=8, chars=4000,
                       max_entries=8, max_chars=4000):
    """Exact child locators or Unicode text windows, never a semantic summary."""
    if type(start) is not int or start < 0:
        raise ValueError('Read start must be a nonnegative integer')
    if type(limit) is not int or not 1 <= limit <= max_entries:
        raise ValueError(f'Read limit must be an integer from 1 to {max_entries}')
    if type(chars) is not int or not 1 <= chars <= max_chars:
        raise ValueError(f'Read chars must be an integer from 1 to {max_chars}')
    if isinstance(value, str):
        if start > len(value):
            raise ValueError('Read start is outside the string')
        stop = min(start + chars, len(value))
        return dict(kind='text', text=value[start:stop], start=start,
                    total_characters=len(value), next_start=stop if stop < len(value) else None)
    if isinstance(value, (dict, list)):
        items = list(value.items()) if isinstance(value, dict) else list(enumerate(value))
        if start > len(items):
            raise ValueError('Read start is outside the container')
        stop = min(start + limit, len(items))
        return dict(kind='object' if isinstance(value, dict) else 'array', start=start,
                    total_children=len(items), next_start=stop if stop < len(items) else None,
                    children=[{'pointer': pointer + '/' + str(key).replace('~', '~0').replace('/', '~1'),
                               'kind': 'object' if isinstance(child, dict) else 'array' if
                               isinstance(child, list) else 'text' if isinstance(child, str)
                               else 'scalar'} for key, child in items[start:stop]])
    if start:
        raise ValueError('Scalar values require start=0')
    return dict(kind='scalar', value=value)


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
