Method planning development cases

Read docs/decisions/2026-11-03-harness-redesign.txt first.
development_cases.json names nine cases drawn from17already processed source
occurrences. Each case records the required behavior and a failure to detect.
The original source/attempt pointers are preserved. Model-derived scope and
state labels are marked as proposals; they do not establish scientific identity.

These are prospective development checks for the replacement method planner.
No native model was run to create this file, no identities were canonically
merged, and no planner performance score has been measured. A manually assigned
mapping may test scheduling, but cannot evaluate an LLM identity resolver.

First inspect source assignments and question-specific update actions. Then
evaluate actual planner/reviewer behavior on a small case group. Do not feed the
required_behavior field to a worker whose predictions will be evaluated.
