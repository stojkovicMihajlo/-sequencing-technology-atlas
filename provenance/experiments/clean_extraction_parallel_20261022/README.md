# Fresh validation of the revised harness

This is a new source-only run of frozen `f82d93f`, after 2,401 software tests
passed (three skipped). It tests the combined development revision: clearer
source-requirement meanings and concurrent source preparation/readers. It does
not alter baseline job 48932552 or repair one of its candidate graphs.

Both methods use the same registered source bytes, scope, model/effort and review
gates as the baseline. No previous graph, requirements, review, host findings or
scientific correction enters the new plan. The existing strict runner is reused;
only the two fresh packet paths differ. Code-only runtime selection excludes
example graphs, prior experiments, host answers and tests. This is input
separation, not an OS access boundary or protection against model memory.

The original host scientific dimensions and source-check themes stay fixed.
Inspect initial and final candidates, normal within-run detections and any
remaining unsupported additions/omissions separately. Report actual stage
timings, requirement-scope problems, tool failures and graph-consumer checks.
This is a combined-release regression, not a factorial ablation: elapsed-time
differences cannot be assigned solely to scheduling, and model judgments remain
fallible. Both methods are exposed development cases, not held-out families.

The [comparison record](comparison.json), [source-only plan](plan.json) and
[runtime receipt](runtime_receipt.json) are frozen before submission. Original
host source-check files remain local and are never native inputs. Native runs
use Qiu CPU Slurm, `gpt-6.1-sol` / `xhigh`, the existing Codex login and managed
sandbox/approval policy. The EXIT archive is preserved outside scratch. No
canonical promotion follows automatically.
