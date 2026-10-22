# Diagnose failed transport without changing scientific acceptance

The revised clean DBiT extraction in Qiu job 48941049 stopped during its
information-reader stage. The archived CLI 0.163.0 terminal `turn.failed` event
reports model capacity. The previous state exposed only a generic unsuccessful
process error. This is different from an invalid graph, missing evidence, a
negative scientific review or a Sherlock failure.

The development worker now records a nonaccepting terminal diagnosis alongside
the existing failed attempt. It retains event location and archive hashes, not
raw provider text in the summary. Only the exact observed capacity message is
recognized; other forms remain unknown. Model/tool output cannot supply this
classification. Later turn/thread starts invalidate earlier terminal causes,
and conflicting available thread identities prevent attribution.

This adds no retry, model switch, acceptance waiver or source repair. Failed and
cancelled preparation artifacts cannot feed integration. Missing reported usage
is not interpreted as zero usage. Frozen runtimes and the failed native attempt
are unchanged; replaying the classifier on that archive is a diagnostic replay,
not a successful new extraction.

The tiny test fixture preserves the actual terminal tail and its original line
and file hashes, without paper text or credentials. Synthetic controls exercise
recovered intermediate errors, later unfinished turns, identity conflicts,
untrusted messages, malformed archives and unchanged rejection/export behavior.
The complete development suite passed **2,453 tests, three skipped** in 112.90s
before the subsequent reviewer-instruction experiment.
