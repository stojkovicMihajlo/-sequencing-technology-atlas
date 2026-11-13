"""Freeze a tiny experiment payload; never connects remotely or launches a model."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

OWN = Path(__file__).resolve().parent
REPOSITORY = OWN.parents[1]


def prepare(output):
    output = Path(output).resolve()
    if not output.is_relative_to(OWN):
        raise ValueError('Prepare only inside the owned experiment directory')
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    for name in ('live_pilot.py','test_live_pilot.py','live_targets.json','live_pilot.sbatch','preserve_bytes.py','test_preserve_bytes.py'):
        source=OWN/name;target=output/name;shutil.copyfile(source,target)
        files[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    for name in ('src/genomics_discovery/execution/codex_worker.py',
                 'src/genomics_discovery/illumina/sources.py','scripts/sherlock/environment.sh',
                 'scripts/sherlock/atlas-account.sh'):
        source=REPOSITORY/name;target=output/'runtime'/name
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        files['runtime/'+name]=hashlib.sha256(target.read_bytes()).hexdigest()
    for package in ('genomics_discovery','genomics_discovery/execution','genomics_discovery/illumina'):
        name='runtime/src/'+package+'/__init__.py'
        target=output/name;target.write_text('"""Isolated support snapshot for the abstract intake pilot."""\n')
        files[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    # Namespace-only support snapshot: the copied worker/source helper are the
    # existing implementations, not rewritten experiment transport/downloaders.
    manifest={'format_version':'abstract_pilot_payload/1','files':files,
              'model':'gpt-6.1-sol','effort':'xhigh','native_calls_if_successful':3,
              'token_quota':None,'fulltext_requests':0,
              'source_runtime_boundary':'Minimal frozen support snapshot of the existing CodexWorker, rate-guarded fetch/parse_pubmed and Sherlock environment. Not a new full-atlas release.',
              'expected_labels_in_payload':False}
    (output/'PAYLOAD_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    archive=output.with_suffix('.tar.gz')
    with tarfile.open(archive,'x:gz') as handle:
        handle.add(output,arcname=output.name)
    return {'release':str(output),'archive':str(archive),
            'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(prepare(args.output),indent=2))
