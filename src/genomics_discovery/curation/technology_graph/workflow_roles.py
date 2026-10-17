"""Overlapping navigation questions, not a compulsory protocol taxonomy.

These are a working convention for discovery. They assign neither scientific
identity nor workflow order. Mechanisms remain in source-bound operation records.
"""

ROLES = {
    "specimen_preparation": {
        "label": "Prepare or preserve the specimen",
        "question": "How is the specimen collected, preserved, accessed or prepared while retaining the biological information needed for the assay?",
    },
    "selection_partitioning": {
        "label": "Select, capture or partition material",
        "question": "How are target molecules, cells or locations selected, captured, separated or partitioned?",
    },
    "molecular_processing": {
        "label": "Transform or copy molecules",
        "question": "How are molecules fragmented, joined, copied, amplified or converted into material that a subsequent operation can use?",
    },
    "information_encoding": {
        "label": "Record or retain identity and relationships",
        "question": "How is molecular, cell, sample, spatial, temporal or interaction identity recorded, linked or preserved through processing?",
    },
    "readout": {
        "label": "Acquire an instrument signal",
        "question": "What signal does the instrument measure and how does that signal retain information about the biological target?",
    },
    "interpretation": {
        "label": "Decode or infer the biological measurement",
        "question": "How are signals decoded, assigned, corrected or interpreted to obtain the intended biological measurement?",
    },
    "quality_control": {
        "label": "Check or calibrate the measurement",
        "question": "What is checked or calibrated to decide whether to proceed, adjust processing or trust the resulting measurement?",
    },
}


def catalogue():
    return {
        "format_version": "workflow_search_roles/1.0",
        "roles": [{"id": key, **value} for key, value in ROLES.items()],
        "boundary": "Overlapping optional search lenses, not annotations, mandatory stages, "
        "mutually exclusive categories or evidence of shared mechanism. An operation may "
        "serve several roles at any workflow position. Free-text questions are also accepted. "
        "Chemistry describes mechanisms across roles; it is not a competing preparation stage.",
    }
