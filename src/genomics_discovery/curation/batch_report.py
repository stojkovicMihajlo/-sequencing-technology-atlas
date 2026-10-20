"""Read-only progress/usage view; a report does not admit scientific records."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from .extraction_batch import status


def report(run: Path) -> dict:
    snapshot = status(run)
    methods, total_usage = [], Counter()
    now = datetime.now(timezone.utc)
    for mid, row in snapshot['jobs'].items():
        item = {'id': mid, 'status': row['status'],
                'input_health': snapshot['input_health'][mid],
                'attempts': len(row['attempts']), 'stages': [], 'usage': {}}
        usage = Counter()
        for attempt in row['attempts']:
            path = Path(attempt['run']) / 'state.json'
            if not path.exists():
                continue
            try:
                runtime = json.loads(path.read_bytes())
                for stage in runtime.get('attempts', []):
                    started = datetime.fromisoformat(stage['started_at'])
                    end = datetime.fromisoformat(stage['finished_at']) if stage.get('finished_at') else now
                    item['stages'].append({
                        'role': stage['role'], 'status': stage['status'],
                        'seconds_since_start': round(max(0, (end - started).total_seconds()), 1),
                        'directory': stage['directory'],
                        'thread_id': stage.get('thread_id'),
                        'usage': stage.get('usage', {}),
                    })
                    usage.update({k:v for k,v in stage.get('usage', {}).items()
                                  if isinstance(v, (int, float)) and not isinstance(v, bool)})
            except (OSError, ValueError, KeyError) as exc:
                item.setdefault('read_errors', []).append({'path': str(path), 'error': str(exc)})
        item['usage'] = dict(usage)
        total_usage.update(usage)
        item['current_stage'] = item['stages'][-1] if item['stages'] else None
        for key in ('reason', 'next_evidence', 'error', 'failure_kind'):
            if key in row:
                item[key] = row[key]
        methods.append(item)
    return {'format_version': 'batch_progress_report/1.0', 'run': snapshot['run'],
            'status': snapshot['status'], 'observed_at': now.isoformat(),
            'counts': dict(Counter(row['status'] for row in methods)),
            'usage_from_recorded_stages': dict(total_usage), 'methods': methods,
            'boundary': 'Read-only snapshot. Elapsed time does not prove worker activity; '
                        'in-progress usage may be absent. Usage is cumulative across native '
                        'turn inputs (including cached input), not unique document tokens or cost. '
                        'Supported status is not graph admission or expert validation.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    print(json.dumps(report(args.run), indent=2))


if __name__ == '__main__':
    main()
