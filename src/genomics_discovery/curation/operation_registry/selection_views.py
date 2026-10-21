"""Navigation projections; original scientific records and evidence remain intact."""

from ..evidence_views import evidence_navigation, inspect_pointer


def selection_overview(view):
    """Preserve all non-evidence fields verbatim; replace evidence lists with locators."""
    return {'format_version': 'selection_overview/1.0',
        'occurrence_sha256': view['occurrence_sha256'],
        'record': evidence_navigation(view['record'], '/record'),
        'boundary': 'Navigation view, not evidence review. All non-evidence fields are unchanged. '
                    'Evidence quotations and rationales are omitted here, available verbatim through '
                    'inspect --pointer. Reopen cited source passages; this view does not establish support.'}
