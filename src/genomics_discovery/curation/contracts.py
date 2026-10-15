"""Load versioned scientific instructions without depending on prior run folders.

Version 1 preserves the original instruction bytes, including their provenance
headers. Those identify the contract's origin, not approval of a current run.
"""

from importlib.resources import files

CONTRACT_VERSION = "genomics-protocol-contracts/1.0"
_RESOURCES = {
    "extraction": "source_extraction_v1.txt",
    "recovery": "evidence_recovery_v1.txt",
    "output_shape": "output_contract_v1.txt",
}


def load_contracts() -> dict[str, str]:
    """Return instructions to snapshot into a run; no scientific findings are loaded."""
    directory = files("genomics_discovery.curation").joinpath("instructions")
    contracts = {key: directory.joinpath(name).read_text(encoding="utf-8")
                 for key, name in _RESOURCES.items()}
    if any(not value.strip() for value in contracts.values()):
        raise ValueError("Scientific instruction resource is empty")
    return contracts
