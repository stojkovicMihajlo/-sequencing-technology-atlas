# scSTAMP-seq: completed prototype example

Open [overview.html](overview.html) after downloading/cloning this repository.
It is a standalone reading view: expand an Operation to inspect its inputs,
outputs, conditions, resources and supporting evidence. Internal links open the
corresponding State. The authoritative extraction is [workflow.json](workflow.json).
Both files are byte-for-byte copies of Qiu job **48925060** outputs.

The extraction covers the **2025-10-02 bioRxiv v1** implementation,
DOI [10.1101/2025.10.01.679703](https://doi.org/10.1101/2025.10.01.679703),
not the later journal version. There are 63 Operations, 80 States, 168 Flows and
13 Modules across plate RNA, droplet RNA and MAT routes. Card order is a reading
order, not an executable schedule. Conditional edges must not be silently mixed.

The model review returned `supported` for a general method description. Fourteen
overlapping gap records remain; this is not an execution-ready SOP or an expert
accuracy certificate. Source packets, original papers, native traces and review
sidecars are preserved in the durable Sherlock archive rather than distributed
as full-paper contents here. Original absolute provenance paths are retained.

| File | SHA-256 of exact bytes |
| --- | --- |
| workflow.json | `9005fd90568e133b9854fe668bda88b7f386dd9da8e789e04ea71d2d79f643ec` |
| overview.html | `d9bfafbe82efe394e1094bcb8c22c2aa7a0fa2e70eb65b92849317dba827c63c` |

The canonical Workflow digest (normalized model serialization) is
`32652da533c2d2ff2858d483bdad713314fd48787bda9e86c3e0b81d851dab69`.
It differs from the file hash intentionally. See the
[experiment report](../../experiments/scstamp_seq_20261021/README.md) for the
repair audit, reviewer controls and limitations.
