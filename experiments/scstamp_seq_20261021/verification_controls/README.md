# Local verification controls

These controls measure whether a fresh reader rejects specific unsupported
assertions and notices a missing required operation, while retaining supported
content. They do not certify the complete scSTAMP graph or validate the production
reviewer. Labels were checked against the frozen source passages by a separate
scientific audit before inference; they remain model-authored, not expert gold.
See [independent_label_audit.json](independent_label_audit.json) for the final
confirmation and exact definition hash. The earlier pending status inside the
definition records its preparation state, not a later uncompleted review.

The eight cases form four pairs:

| Local field or graph | Controlled change | Expected distinction |
| --- | --- | --- |
| Unsampled first-PCR remainder | Add a categorical disposal claim | An unreported fate is not established. |
| Plate capture-primer resources | Change the stated six-nucleotide UMI to eight | Contradicts the primary plate-RNA method. |
| Spatial read decoding | Exchange read 1 and read 2 identity assignments | Contradicts the stated read structure. |
| Pooling and first SPRI partition | Delete the partition operation and incident connections | Remaining true statements do not satisfy the explicit local coverage assertion. |

An ambiguous MAT product description was removed from scored controls before
inference. It needs clarification in the actual extraction, but ambiguity is not a
defensible hard label of chemical fabrication. Context was expanded to support all
molecular identities retained in the positive coverage graph.

The helper binds exact original field values, source passages, packet bytes and
mutations. It removes a selected operation without inventing a bypass. Reader
input omits expected labels, original/changed markers and source paths; this is
input separation, not a claim of filesystem isolation. The existing runner
replays source integrity, verifies complete native archives and checks every case
ID and value hash. These are deterministic checks. Source entailment is measured
against the supplied labels, not proved by those checks.

One native session uses the existing basic grounding prompt. A second uses the
shared production `CONTRACT` and existing factual-field instructions. Both receive
the same local-coverage task definition, cases and result schema, with no retries
or output repairs. This is a local prompt comparison with at most two fresh native
sessions, **not** execution of the full production review prompt or a calibrated
estimate of its sensitivity. Each session may use tools across multiple turns.

The deterministic `control_gate` requires complete native transport, no missing
judgments or abstentions, rejection of every negative control, and retention of
every supported control. It reports exact negative-class agreement separately.
Always approving, rejecting or abstaining cannot pass. Four negative examples
cannot establish a low error rate across full papers or new families.

Prepare again on Sherlock using its exact packet path, then run the existing
diagnostic inside Slurm:

```sh
PYTHONPATH=src python scripts/benchmark/prepare_workflow_controls.py \
  --workflow /path/to/frozen/workflow.json \
  --packet /path/to/frozen/source_packet.json \
  --definitions experiments/scstamp_seq_20261021/verification_controls/definitions.json \
  --output /path/to/new/prepared-controls

PYTHONPATH=src python scripts/benchmark/run_grounding_diagnostic.py \
  --cases /path/to/new/prepared-controls/cases.json \
  --labels /path/to/new/prepared-controls/labels.json \
  --prompt-directory /path/to/new/prepared-controls/prompts \
  --output /path/to/new/native-run
```

The rationale follows [ALCE's separation of citation evaluation and correctness](https://aclanthology.org/2023.emnlp-main.398/)
and [WiCE's subclaim-level support distinctions](https://aclanthology.org/2023.emnlp-main.470/).
ALCE's [pinned evaluator](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/eval.py#L245)
first checks citation indices and then invokes a separate entailment model. We
reuse that separation of responsibilities, without copying its scorer or assuming
its general-domain model is suitable for biochemical protocols. The omission case
additionally tests a representation's explicit coverage; sentence entailment alone
does not detect missing protocol content.
