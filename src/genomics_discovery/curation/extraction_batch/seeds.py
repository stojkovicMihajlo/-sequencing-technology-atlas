"""Freeze explicit same-packet runtime seeds; inherit no scientific verdict.

The copied candidate/review are repair inputs. Reused source obligations keep
their original native-owner path as the runtime input; an unowned batch snapshot
is an audit copy only. Every new result still requires the normal fresh review.
"""

from pathlib import Path

from ..workflow_extraction.models import digest
from ..workflow_extraction.sources import load_packet
from .storage import atomic_bytes, check_pins, read, sha, write


def freeze_seed(origin: Path, mode: str, packet: dict, output: Path, config):
    # Local imports avoid a controller import cycle and reuse the same successful
    # artifact validation as ordinary operational restart recovery.
    from .controller import _recover, SCIENTIFIC
    from ..technology_graph.admission import _source_obligations_lineage

    origin = Path(origin).resolve()
    state_path, config_path = origin / "state.json", origin / "config.json"
    state, source_config = read(state_path), read(config_path)
    if state.get("format_version") != "workflow_run/1.0" or state.get("status") not in SCIENTIFIC:
        raise ValueError("Explicit seeds require a terminal scientific workflow runtime")
    if state.get("config_sha256") != sha(config_path):
        raise ValueError("Seed runtime config binding changed")
    packet_path = origin / "source_packet.json"
    source_packet = load_packet(packet_path)
    if (source_config.get("source_packet_sha256") != sha(packet_path)
            or source_config.get("source_packet_digest") != digest(source_packet)):
        raise ValueError("Seed source packet differs from its runtime checkpoint")
    if digest(source_packet) != digest(packet):
        raise ValueError("Seed requires the same canonical source packet; use fresh extraction for changed sources")
    # This is input validation, not a claim of current acceptance. A source-sidecar
    # rejection cannot be substituted for a negative primary WorkflowReview.
    primary_config = config.model_copy(update={"require_source_coverage": False})
    candidate, pair, _, _ = _recover({"attempts": [{"run": str(origin)}]}, source_packet, primary_config)
    if candidate is None or pair is None:
        raise ValueError("Seed lacks an archived validated candidate and primary review")
    workflow_path, workflow = candidate
    review_path = Path(pair["review"])
    if (state.get("workflow_digest") != digest(workflow)
            or state.get("review") != str(review_path)
            or state.get("review_sha256") != sha(review_path)):
        raise ValueError("Seed final workflow/review checkpoint disagrees with archived pair")
    if mode == "repair" and (pair["primary_outcome"] != "needs_revision" or not config.max_revisions):
        raise ValueError("repair seed requires primary needs_revision and max_revisions=1; coverage-only rejection needs explicit review_only")
    if mode not in {"repair", "review_only"}:
        raise ValueError("Unknown explicit seed mode")
    if source_config.get("require_source_coverage") and not config.require_source_coverage:
        raise ValueError("A source-first seed cannot silently disable source coverage")
    files = {path for path in origin.rglob("*") if path.is_file()}
    files.update(Path(s["original_path"]) for s in source_packet["sources"])
    obligations = None
    source_artifact = state.get("artifacts", {}).get("source_obligations")
    if source_artifact is not None:
        if not config.require_source_coverage:
            raise ValueError("Source-obligations lineage requires source coverage")
        # Preflight the graph gate's exact native-owner/copy lineage. It accepts
        # negative candidate outcomes without promoting them. The gate collects
        # every inherited owner/archive/packet dependency for immutable pins.
        _source_obligations_lineage(origin, state, source_config, source_packet, files)
        obligations = Path(source_artifact["path"]).resolve()
    pins = {str(path):sha(path) for path in files}
    check_pins(pins)
    frozen = {"mode": mode, "runtime_run": str(origin),
              "source_packet_digest": digest(source_packet),
              "primary_outcome": pair["primary_outcome"],
              "scientific_verdict_inherited": False}
    copies = {"workflow":workflow_path, "review":review_path, "source_packet":packet_path}
    if obligations is not None:
        copies["source_obligations_snapshot"] = obligations
        frozen["source_obligations_origin"] = str(obligations)
    for key, source in copies.items():
        destination = output / f"{key}.json"
        atomic_bytes(destination, source.read_bytes())
        if sha(destination) != pins[str(source)]:
            raise ValueError("Seed artifact changed during snapshot")
        pins[str(destination)] = sha(destination)
        frozen[key] = str(destination)
    check_pins(pins)
    write(output / "seed.json", frozen)
    pins[str(output / "seed.json")] = sha(output / "seed.json")
    return frozen, pins
