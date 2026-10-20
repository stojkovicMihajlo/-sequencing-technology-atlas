"""Native jobs retain inspectable artifacts and bound their own subprocesses."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
from datetime import datetime
from pathlib import Path

import pytest

from genomics_discovery.curation import codex_worker
from genomics_discovery.curation.codex_worker import CodexWorker, assess_archived_attempt


def fake_codex(tmp_path: Path, body: str) -> str:
    executable = tmp_path / "fake codex"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "args = sys.argv[1:]\n"
        "prompt = sys.stdin.read()\n"
        "output = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
        + body,
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return str(executable)


async def wait_until_exists(path: Path) -> None:
    async with asyncio.timeout(5):
        while not path.exists():
            await asyncio.sleep(0.01)


async def test_no_time_cutoff_still_archives_elapsed_time_and_completion(tmp_path):
    executable = fake_codex(tmp_path,
        "time.sleep(0.15)\n"
        "print(json.dumps({'type':'thread.started','thread_id':'untimed-test'}))\n"
        "print(json.dumps({'type':'turn.completed','usage':{'output_tokens':1}}))\n"
        "output.write_text('Completed without a host cutoff')\n")
    result = await CodexWorker(executable, timeout_seconds=None).run("Task", tmp_path / "untimed")
    assert result.exit_code == 0 and result.error is None
    assert result.elapsed_seconds >= .15
    assert assess_archived_attempt(tmp_path / "untimed").complete


@pytest.mark.asyncio
async def test_native_worker_archives_flags_prompt_events_and_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-must-not-leak")
    monkeypatch.setenv("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "parent-origin")
    monkeypatch.setenv("WORKER_TEST_SECRET", "not-in-command-or-result")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-archive")
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("SLURM_JOB_ID", "test-allocation-123")
    executable = fake_codex(
        tmp_path,
        "assert 'CODEX_THREAD_ID' not in os.environ\n"
        "assert 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE' not in os.environ\n"
        "assert os.environ['WORKER_TEST_SECRET'] == 'not-in-command-or-result'\n"
        "assert prompt == 'Read α, then write. $(never execute)'\n"
        "assert pathlib.Path.cwd() == output.parent\n"
        "assert args[0] == 'exec' and args[-1] == '-'\n"
        "assert args[args.index('--sandbox') + 1] == 'workspace-write'\n"
        "assert 'approval_policy=\"on-request\"' in args\n"
        "assert 'approvals_reviewer=\"auto_review\"' in args\n"
        "assert '--dangerously-bypass-approvals-and-sandbox' not in args\n"
        "assert 'danger-full-access' not in args\n"
        "assert args[args.index('--disable') + 1] == 'multi_agent'\n"
        "assert 'multi_agent_v2' in args\n"
        "assert '--ignore-user-config' in args and '--ephemeral' in args\n"
        "assert '--json' in args and '--skip-git-repo-check' in args\n"
        "assert 'model_reasoning_effort=\"xhigh\"' in args\n"
        "assert 'web_search=\"live\"' in args\n"
        "assert 'sandbox_workspace_write.network_access=true' in args\n"
        "assert args[args.index('--model') + 1] == 'gpt-6.1-sol'\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': 'job-123'}))\n"
        "print('non-JSON diagnostic')\n"
        "print(json.dumps({'type': 'item.completed', 'usage': {'input_tokens': 999}}))\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': "
        "{'input_tokens': 20, 'cached_input_tokens': 5, 'output_tokens': 3}}))\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': "
        "{'input_tokens': 7, 'cached_input_tokens': 2, 'output_tokens': 4}}), end='')\n"
        "sys.stderr.write('diagnostic retained')\n"
        "output.write_text('Finished')\n",
    )
    attempt = tmp_path / "attempt one"
    result = await CodexWorker(executable=executable).run(
        "Read α, then write. $(never execute)", attempt
    )
    assert result.exit_code == 0 and result.error is None
    assert result.thread_id == "job-123"
    assert result.usage == {"input_tokens": 27, "cached_input_tokens": 7, "output_tokens": 7}
    assert result.elapsed_seconds > 0
    assert {path.name for path in attempt.iterdir()} == {
        "prompt.txt", "command.json", "started.json", "events.jsonl", "stderr.log", "final.txt",
        "result.json"
    }
    assert (attempt / "prompt.txt").read_text() == "Read α, then write. $(never execute)"
    assert "non-JSON diagnostic" in (attempt / "events.jsonl").read_text()
    assert (attempt / "stderr.log").read_text() == "diagnostic retained"
    command = json.loads((attempt / "command.json").read_text())
    assert command["argv"][0] == executable and command["cwd"] == str(attempt)
    assert not any("bypass" in item for item in command["argv"])
    assert command["authentication"]["openai_api_key_present"] is True
    assert command["authentication"]["codex_api_key_present"] is False
    assert not any("parent-must-not-leak" in path.read_text() for path in attempt.iterdir())
    assert "not-in-command-or-result" not in (attempt / "command.json").read_text()
    assert "not-in-command-or-result" not in (attempt / "result.json").read_text()
    assert not any("test-key-never-archive" in path.read_text() for path in attempt.iterdir())
    started = json.loads((attempt / "started.json").read_text())
    assert started["hostname"] == socket.gethostname()
    assert started["slurm_job_id"] == "test-allocation-123"
    assert started["pid"] > 0 and started["spawned"] is True
    assert datetime.fromisoformat(started["started_at"]).tzinfo is not None
    if os.name == "posix":
        assert started["process_group_id"] == started["pid"]
    if started["process_start_identity"] is not None:
        assert started["process_start_identity"]["kind"] == "ps_lstart"


@pytest.mark.asyncio
async def test_configured_model_effort_and_disabled_search(tmp_path):
    executable = fake_codex(
        tmp_path,
        "assert args[args.index('--model') + 1] == 'configured-model'\n"
        "assert 'model_reasoning_effort=\"xhigh\"' in args\n"
        "assert 'web_search=\"disabled\"' in args\n"
        "assert 'sandbox_workspace_write.network_access=false' in args\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': {}}))\n"
        "output.write_text('Done')\n",
    )
    result = await CodexWorker(
        executable, model="configured-model", effort="xhigh", web_search=False,
        network_access=False,
    ).run("Task", tmp_path / "attempt")
    assert result.error is None


@pytest.mark.asyncio
async def test_failure_preserves_stderr_and_returns_result_not_stack_trace(tmp_path):
    executable = fake_codex(tmp_path, "sys.stderr.write('failure detail'); sys.exit(7)\n")
    attempt = tmp_path / "attempt"
    result = await CodexWorker(executable).run("Task", attempt)
    assert result.exit_code == 7 and "code 7" in result.error
    assert (attempt / "stderr.log").read_text() == "failure detail"
    assert (attempt / "final.txt").is_file()
    assert json.loads((attempt / "result.json").read_text())["exit_code"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("body,match", [
    ("pass\n", "without final.txt"),
    ("output.write_text('')\n", "empty final.txt"),
    (
        "print(json.dumps({'type': 'turn.failed', 'error': {'message': 'private detail'}}))\n"
        "output.write_text('partial')\n",
        "turn.failed",
    ),
])
async def test_transport_does_not_mistake_incomplete_output_for_success(tmp_path, body, match):
    attempt = tmp_path / "attempt"
    result = await CodexWorker(fake_codex(tmp_path, body)).run("Task", attempt)
    assert result.exit_code == 0 and match in result.error
    assert "private detail" not in json.dumps(result.__dict__)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    "pass\n",
    "print(json.dumps({'type': 'item.completed', 'item': "
    "{'type': 'agent_message', 'text': 'Finished'}}))\n",
    "print(json.dumps({'type': 'turn.completed'}))\n",
    "print('{\"type\":\"turn.completed\",', end='')\n",
])
async def test_nonempty_final_requires_a_valid_completed_turn(tmp_path, body):
    attempt = tmp_path / "attempt"
    result = await CodexWorker(fake_codex(tmp_path, body + "output.write_text('Finished')\n")).run(
        "Task", attempt
    )
    assert result.exit_code == 0
    assert "without a completed turn" in result.error
    assert assess_archived_attempt(attempt).complete is False


@pytest.mark.asyncio
@pytest.mark.parametrize("event_types,match", [
    (["error", "error", "turn.completed"], None),
    (["error"], "emitted error"),
    (["turn.completed", "error"], "emitted error"),
    (["turn.failed", "turn.completed"], "turn.failed"),
    (["error", "turn.failed", "turn.completed"], "turn.failed"),
    (["turn.completed", "turn.failed"], "turn.failed"),
    (["turn.completed", "turn.started"], "unfinished turn"),
])
async def test_terminal_events_resolve_reconnects_but_never_failed_turns(
    tmp_path, event_types, match
):
    events = [
        {"type": event_type, "message": "Reconnecting... private detail",
         "usage": {"input_tokens": 13, "cached_input_tokens": 5, "output_tokens": 2}}
        for event_type in event_types
    ]
    executable = fake_codex(
        tmp_path,
        f"events = {events!r}\n"
        "for event in events: print(json.dumps(event))\n"
        "output.write_text('Finished')\n",
    )
    attempt = tmp_path / "attempt"
    result = await CodexWorker(executable).run("Task", attempt)
    assert result.exit_code == 0
    if match is None:
        assert result.error is None
    else:
        assert match in result.error
    expected_usage = {"input_tokens": 13, "cached_input_tokens": 5, "output_tokens": 2}
    assert result.usage == (expected_usage if "turn.completed" in event_types else {})
    assert "private detail" not in (attempt / "result.json").read_text()
    assert "private detail" in (attempt / "events.jsonl").read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending,match", [
    ("sys.exit(7)\n", "code 7"),
    ("pass\n", "without final.txt"),
    ("output.write_text('')\n", "empty final.txt"),
])
async def test_recovered_stream_does_not_override_process_or_final_failure(tmp_path, ending, match):
    executable = fake_codex(
        tmp_path,
        "print(json.dumps({'type': 'error', 'message': 'Reconnecting...'}))\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 13}}))\n"
        + ending,
    )
    result = await CodexWorker(executable).run("Task", tmp_path / "attempt")
    assert match in result.error
    assert result.usage == {"input_tokens": 13}


def write_completed_archive(attempt: Path) -> None:
    attempt.mkdir()
    events = [
        {"type": "thread.started", "thread_id": "archived-job"},
        {"type": "turn.started"},
        {"type": "error", "message": "Reconnecting... private detail"},
        {"type": "turn.completed", "usage": {"input_tokens": 13, "output_tokens": 2}},
    ]
    (attempt / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events))
    (attempt / "result.json").write_text(json.dumps({
        "exit_code": 0, "thread_id": "archived-job", "usage": events[-1]["usage"],
        "elapsed_seconds": 2.0, "error": "Codex emitted error; see events.jsonl",
    }))
    (attempt / "final.txt").write_text("Finished")


def test_archive_assessment_recovers_completed_reconnect_without_mutation(tmp_path):
    attempt = tmp_path / "attempt"
    write_completed_archive(attempt)
    before = {path.name: path.read_bytes() for path in attempt.iterdir()}
    assessment = assess_archived_attempt(attempt)
    assert assessment.complete is True and assessment.error is None
    assert assessment.thread_id == "archived-job"
    assert assessment.completed_turns == 1
    assert assessment.usage == {"input_tokens": 13, "output_tokens": 2}
    assert before == {path.name: path.read_bytes() for path in attempt.iterdir()}


@pytest.mark.parametrize("result_changes,match", [
    ({"exit_code": 124, "error": "Codex exceeded the timeout"}, "did not exit successfully"),
    ({"exit_code": -15, "error": "Codex attempt cancelled"}, "did not exit successfully"),
    ({"exit_code": 7}, "did not exit successfully"),
    ({"exit_code": False}, "Invalid archived result"),
    ({"error": "Codex transport failed (OSError); inspect attempt archives"}, "nonrecoverable"),
    ({"thread_id": "different-job"}, "disagrees"),
    ({"usage": {"input_tokens": 1}}, "disagrees"),
    ({"elapsed_seconds": float("nan")}, "Invalid archived result"),
])
def test_archive_assessment_rejects_failed_or_inconsistent_result(tmp_path, result_changes, match):
    attempt = tmp_path / "attempt"
    write_completed_archive(attempt)
    result_file = attempt / "result.json"
    result = json.loads(result_file.read_text())
    result.update(result_changes)
    result_file.write_text(json.dumps(result))
    assessment = assess_archived_attempt(attempt)
    assert assessment.complete is False and match in assessment.error


@pytest.mark.parametrize("suffix,match", [
    ('\n{"type": "error", "message": "private detail"}', "emitted error"),
    ('\n{"type": "turn.failed", "error": {"message": "private detail"}}', "turn.failed"),
    ('\n{"type": "turn.started"}', "unfinished turn"),
    ('\n{"type": "turn.completed"}', "invalid JSONL"),
    ('\n{"type":', "invalid JSONL"),
    ('\n[]', "invalid JSONL"),
])
def test_archive_assessment_rejects_unfinished_or_malformed_events(tmp_path, suffix, match):
    attempt = tmp_path / "attempt"
    write_completed_archive(attempt)
    with (attempt / "events.jsonl").open("a") as handle:
        handle.write(suffix)
    assessment = assess_archived_attempt(attempt)
    assert assessment.complete is False and match in assessment.error
    assert "private detail" not in assessment.error


@pytest.mark.parametrize("file_name,contents,match", [
    ("events.jsonl", "", "no completed turn"),
    ("result.json", "{}", "Invalid archived result"),
    ("result.json", "null", "Invalid archived result"),
    ("result.json", "{", "missing or unreadable"),
    ("final.txt", "", "no nonempty final"),
    ("final.txt", None, "no nonempty final"),
    ("events.jsonl", None, "missing or unreadable"),
])
def test_archive_assessment_rejects_missing_or_incomplete_evidence(
    tmp_path, file_name, contents, match
):
    attempt = tmp_path / "attempt"
    write_completed_archive(attempt)
    if contents is None:
        (attempt / file_name).unlink()
    else:
        (attempt / file_name).write_text(contents)
    assessment = assess_archived_attempt(attempt)
    assert assessment.complete is False and match in assessment.error


@pytest.mark.asyncio
async def test_executable_missing_still_has_complete_archive_layout(tmp_path):
    attempt = tmp_path / "attempt"
    result = await CodexWorker(str(tmp_path / "missing-codex")).run("Task", attempt)
    assert result.exit_code == 127 and "FileNotFoundError" in result.error
    assert all((attempt / name).is_file() for name in codex_worker._ARCHIVES)
    assert json.loads((attempt / "started.json").read_text())["spawned"] is False


@pytest.mark.asyncio
async def test_reusing_attempt_is_rejected_without_erasing_previous_evidence(tmp_path):
    executable = fake_codex(
        tmp_path,
        "print(json.dumps({'type': 'turn.completed', 'usage': {}}))\n"
        "output.write_text('Done')\n",
    )
    attempt = tmp_path / "attempt"
    worker = CodexWorker(executable)
    await worker.run("Original", attempt)
    original = {path.name: path.read_bytes() for path in attempt.iterdir()}
    with pytest.raises(FileExistsError):
        await worker.run("Replacement", attempt)
    assert {path.name: path.read_bytes() for path in attempt.iterdir()} == original


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group isolation")
async def test_timeout_signals_only_the_owned_process_group(tmp_path, monkeypatch):
    attempt = tmp_path / "attempt"
    executable = fake_codex(
        tmp_path,
        "pathlib.Path('pid').write_text(str(os.getpid()))\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': 'timed-job'}), flush=True)\n"
        "time.sleep(60)\n",
    )
    observed = []
    original_killpg = os.killpg

    def record_signal(pid, sig):
        observed.append((pid, sig))
        original_killpg(pid, sig)

    monkeypatch.setattr(codex_worker.os, "killpg", record_signal)
    result = await CodexWorker(executable, timeout_seconds=2).run("Task", attempt)
    pid = int((attempt / "pid").read_text())
    assert result.exit_code == 124 and "timeout" in result.error
    assert result.thread_id == "timed-job"
    assert observed == [(pid, signal.SIGTERM), (pid, signal.SIGKILL)]
    assert pid != os.getpgrp()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group isolation")
@pytest.mark.parametrize("exit_code", [0, 7])
async def test_finished_worker_stops_background_descendants(tmp_path, exit_code):
    attempt = tmp_path / "attempt"
    descendant = (
        "import pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "for i in range(3000):\n"
        " pathlib.Path('heartbeat').write_text(str(i))\n"
        " time.sleep(.02)\n"
    )
    executable = fake_codex(
        tmp_path,
        "import subprocess\n"
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "while not pathlib.Path('heartbeat').exists(): time.sleep(.01)\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': {}}))\n"
        "output.write_text('Finished')\n"
        f"sys.exit({exit_code})\n",
    )
    try:
        result = await CodexWorker(executable).run("Task", attempt)
        assert result.exit_code == exit_code
        assert (result.error is None) == (exit_code == 0)
        await asyncio.sleep(.1)
        heartbeat = (attempt / "heartbeat").read_text()
        await asyncio.sleep(.15)
        assert (attempt / "heartbeat").read_text() == heartbeat
    finally:
        # A failing regression must not leave its own fixture alive.
        started = json.loads((attempt / "started.json").read_text())
        try:
            os.killpg(started["process_group_id"], signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group isolation")
async def test_cancellation_reaps_process_and_archives_then_propagates(tmp_path):
    attempt = tmp_path / "attempt"
    executable = fake_codex(
        tmp_path,
        "print(json.dumps({'type': 'thread.started', 'thread_id': 'cancelled-job'}), flush=True)\n"
        "pathlib.Path('pid.tmp').write_text(str(os.getpid()))\n"
        "pathlib.Path('pid.tmp').replace('pid')\n"
        "time.sleep(60)\n",
    )
    task = asyncio.create_task(CodexWorker(executable).run("Task", attempt))
    await wait_until_exists(attempt / "pid")
    pid = int((attempt / "pid").read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = json.loads((attempt / "result.json").read_text())
    assert result["error"] == "Codex attempt cancelled"
    assert result["exit_code"] == -signal.SIGTERM
    assert (attempt / "events.jsonl").stat().st_size > 0
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group isolation")
async def test_cancellation_during_spawn_does_not_orphan_child(tmp_path, monkeypatch):
    executable = fake_codex(tmp_path, "time.sleep(60)\n")
    attempt = tmp_path / "attempt"
    real_spawn = asyncio.create_subprocess_exec
    launched = asyncio.Event()
    release_handle = asyncio.Event()
    children = []

    async def delayed_handle(*args, **kwargs):
        child = await real_spawn(*args, **kwargs)
        children.append(child)
        launched.set()
        await release_handle.wait()
        return child

    monkeypatch.setattr(codex_worker.asyncio, "create_subprocess_exec", delayed_handle)
    task = asyncio.create_task(CodexWorker(executable).run("Task", attempt))
    await asyncio.wait_for(launched.wait(), timeout=5)
    task.cancel()
    await asyncio.sleep(0)
    release_handle.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)
    assert json.loads((attempt / "result.json").read_text())["error"] == "Codex attempt cancelled"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group isolation")
async def test_cancellation_during_timeout_cleanup_reaps_child_and_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_worker, "_SHUTDOWN_GRACE_SECONDS", 0.3)
    executable = fake_codex(
        tmp_path,
        "import signal\n"
        "signal.signal(signal.SIGTERM, lambda *_: pathlib.Path('term').write_text('seen'))\n"
        "pathlib.Path('pid').write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
    )
    attempt = tmp_path / "attempt"
    ready_for_timeout = asyncio.Event()
    real_wait_for = asyncio.wait_for

    async def wait_after_fixture_ready(awaitable, timeout):
        if timeout == 1:
            # This test targets cancellation during cleanup, not interpreter
            # startup speed under concurrent load. Install the TERM handler first.
            await ready_for_timeout.wait()
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(codex_worker.asyncio, "wait_for", wait_after_fixture_ready)
    task = asyncio.create_task(CodexWorker(executable, timeout_seconds=1).run("Task", attempt))
    pid = None
    try:
        await wait_until_exists(attempt / "pid")
        async with asyncio.timeout(5):
            while not (pid_text := (attempt / "pid").read_text().strip()):
                await asyncio.sleep(.01)
        pid = int(pid_text)
        ready_for_timeout.set()
        await wait_until_exists(attempt / "term")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = json.loads((attempt / "result.json").read_text())
        assert result["error"] == "Codex attempt cancelled"
        assert result["exit_code"] == -signal.SIGTERM
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        # Keep a failing regression test from leaving its own fake worker alive.
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.asyncio
async def test_events_are_streamed_before_process_exits(tmp_path):
    attempt = tmp_path / "attempt"
    executable = fake_codex(
        tmp_path,
        "print(json.dumps({'type': 'thread.started', 'thread_id': 'streamed'}), flush=True)\n"
        "pathlib.Path('started').write_text('yes')\n"
        "while not pathlib.Path('release').exists(): time.sleep(.01)\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': {}}))\n"
        "output.write_text('Done')\n",
    )
    task = asyncio.create_task(CodexWorker(executable).run("Task", attempt))
    await wait_until_exists(attempt / "started")
    async with asyncio.timeout(5):
        while not (attempt / "events.jsonl").read_bytes():
            await asyncio.sleep(0.01)
    assert not task.done()
    (attempt / "release").write_text("yes")
    assert (await task).thread_id == "streamed"


@pytest.mark.asyncio
async def test_large_native_event_is_preserved_without_losing_later_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_worker, "_MAX_EVENT_BYTES", 1024)
    executable = fake_codex(
        tmp_path,
        "print(json.dumps({'type': 'item.completed', 'item': {'text': 'x' * 200000}}))\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 13}}))\n"
        "output.write_text('Done')\n",
    )
    attempt = tmp_path / "attempt"
    result = await CodexWorker(executable).run("Task", attempt)
    assert result.error is None and result.usage == {"input_tokens": 13}
    assert len(json.loads((attempt / "events.jsonl").read_text().splitlines()[0])["item"]["text"]) \
        == 200000


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_timeout_must_be_bounded_and_positive(timeout):
    with pytest.raises(ValueError, match="timeout_seconds"):
        CodexWorker(timeout_seconds=timeout)
