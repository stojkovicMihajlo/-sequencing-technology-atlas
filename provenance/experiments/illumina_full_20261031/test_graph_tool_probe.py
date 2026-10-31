"""Offline tests of native-tool-call observability, not scientific extraction."""
import importlib.util
import json
from pathlib import Path
import shlex


def module():
    spec = importlib.util.spec_from_file_location('graph_probe', Path(__file__).with_name('graph_tool_probe.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_direct_queries_after_environment_setup_are_counted(tmp_path):
    exe, graph = '/env/bin/python', tmp_path / 'graph.json'
    command = shlex.join([exe, '-m', 'genomics_discovery.curation.illumina_graph', 'walk', str(graph), 'state:1'])
    script = 'source /group/environment.sh\nexport X=1\n' + command + ' > route.json\njq . route.json'
    event = {'type': 'item.completed', 'item': {'type': 'command_execution', 'exit_code': 0,
             'command': shlex.join(['/bin/bash', '-c', script])}}
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps(event) + '\n')
    assert module().successful_graph_commands(path, exe, graph) == {'walk'}


def test_failed_embedded_and_wrong_graph_commands_are_not_counted(tmp_path):
    exe, graph = '/env/bin/python', tmp_path / 'graph.json'
    command = shlex.join([exe, '-m', 'genomics_discovery.curation.illumina_graph', 'walk', str(graph), 'state:1'])
    events = []
    for script, code in [(command, 1), ('printf %s ' + shlex.quote(command), 0),
                         ("python - <<'PY'\n" + command + '\nPY', 0),
                         (command.replace('graph.json', 'different.json'), 0)]:
        events.append({'type': 'item.completed', 'item': {'type': 'command_execution', 'exit_code': code,
                       'command': shlex.join(['/bin/bash', '-c', script])}})
    path = tmp_path / 'events.jsonl'
    path.write_text(''.join(json.dumps(e) + '\n' for e in events))
    assert module().successful_graph_commands(path, exe, graph) == set()
