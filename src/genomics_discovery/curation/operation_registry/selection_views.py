"""Navigation projections; original scientific records and evidence remain intact."""

from copy import deepcopy
import re


def inspect_pointer(view, pointer):
    """Resolve an RFC 6901 JSON-string pointer; URI fragments are not accepted."""
    if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
        raise ValueError('Use an empty pointer or a slash-prefixed JSON Pointer')
    value = view
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


def selection_overview(view):
    """Preserve all non-evidence fields verbatim; replace evidence lists with locators."""
    def project(value, pointer):
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                location = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
                if key == 'evidence' and isinstance(child, list):
                    refs = sorted({(e['source_id'], e['passage_id']) for e in child})
                    result[key] = {'pointer': location, 'count': len(child),
                        'support_types': sorted({e['support'] for e in child}),
                        'passages': [{'source_id': s, 'passage_id': p} for s, p in refs]}
                else:
                    result[key] = project(child, location)
            return result
        if isinstance(value, list):
            return [project(child, f'{pointer}/{index}') for index, child in enumerate(value)]
        return value
    return {'format_version': 'selection_overview/1.0',
        'occurrence_sha256': view['occurrence_sha256'],
        'record': project(view['record'], '/record'),
        'boundary': 'Navigation view, not evidence review. All non-evidence fields are unchanged. '
                    'Evidence quotations and rationales are omitted here, available verbatim through '
                    'inspect --pointer. Reopen cited source passages; this view does not establish support.'}
