"""Record delivered full-paper text ranges, not claims of model comprehension."""
import hashlib
import json
from pathlib import Path


def record_read(path, source_file, sources, result):
    if result['start'] is None:
        return
    sid = result['source_id']; text = sources[sid]['text']
    receipt = {'source_id': sid, 'source_registry_sha256': hashlib.sha256(Path(source_file).read_bytes()).hexdigest(),
               'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
               'start': result['start'], 'end': result['start'] + len(result['text']),
               'total_characters': len(text)}
    with Path(path).open('a') as stream:
        stream.write(json.dumps(receipt) + '\n')


def check_full_reads(path, source_file, sources, required_ids):
    receipts = [json.loads(line) for line in Path(path).read_text().splitlines()] if Path(path).exists() else []
    registry_pin = hashlib.sha256(Path(source_file).read_bytes()).hexdigest()
    summary = {}
    for sid in required_ids:
        text = sources[sid]['text']; pin = hashlib.sha256(text.encode()).hexdigest()
        ranges = sorted((r['start'], r['end']) for r in receipts if r['source_id'] == sid
                        and r['source_registry_sha256'] == registry_pin and r['text_sha256'] == pin
                        and 0 <= r['start'] <= r['end'] <= len(text))
        end = 0
        for start, stop in ranges:
            if start > end:
                break
            end = max(end, stop)
        summary[sid] = {'covered_characters_from_start': end, 'total_characters': len(text),
                        'complete_text_delivered': end == len(text), 'text_sha256': pin}
        if end != len(text):
            raise ValueError(f'Full paper text not delivered through the source tool: {sid} ({end}/{len(text)} characters)')
    return {'papers': summary, 'boundary': 'Tool-delivery coverage, not proof of comprehension or scientific correctness.'}
