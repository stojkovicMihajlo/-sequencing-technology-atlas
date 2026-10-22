# Opt-in shell environment for native workers

Status: development-only, default disabled. Existing frozen runtimes, running jobs
and scientific prompts are unchanged. No live Codex probe establishes performance
or Sherlock behavior for this profile yet.

Some native traces used bare `python` and encountered a different interpreter from
the Python running the harness. Project-owned source helpers already use absolute
Python paths. Unrestricted scratch commands still depend on shell startup and
command resolution; another sentence telling the model to choose Python 3 is not
environment enforcement.

`CodexWorker(pin_shell_environment=True)` captures a small explicit environment
profile at construction. It prepends `Path(sys.executable).absolute().parent` to
the existing PATH, preserving virtualenv symlink paths. Existing PYTHONPATH and
LD_LIBRARY_PATH are explicitly retained when present. It configures non-login
shells, disables shell snapshots, and supplies these values through supported
`shell_environment_policy.set` overrides. The existing sandbox, managed approval
configuration and inherited authentication selection remain unchanged. Only the
chosen path variables appear in argv; there is no general environment dump.

The [official OpenAI configuration reference](https://developers.openai.com/codex/config-reference/)
specifies `allow_login_shell=false`: explicit login requests are rejected and an
omitted login flag defaults to non-login. This was also checked against the exact
[0.163.0 command handler](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/src/tools/handlers/unified_exec.rs#L90)
and [shell argument construction](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/src/shell.rs#L21).
The [snapshot renderer](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/shell-command/src/shell_snapshot_render.rs#L13)
respects explicit environment overrides, but snapshots also restore functions and
aliases; this profile disables that replay to remove this source of variability.
No upstream code was copied.

The optional `preflight_pinned_shell_environment` function compares an absolute
interpreter invocation and bare `python` in `/bin/bash -c`, checking resolution,
version and prefix in the same pinned environment. It fails on a mismatch or
unexpected startup output. It does not run automatically inside the worker.
Tests mock this probe; it was not executed on Sherlock or through a model.

This is a bounded launch guarantee, not a guarantee that every Python command uses
one interpreter. BASH_ENV and managed startup behavior remain intact; commands can
explicitly change PATH or name another executable. The profile metadata records
that limitation and whether automatic preflight occurred. No global startup files,
aliases, credentials, managed hooks or security policies are edited. Default-off
behavior preserves legacy argv and command-record structure. A future release must
perform and archive its environment preflight before adopting the profile.
