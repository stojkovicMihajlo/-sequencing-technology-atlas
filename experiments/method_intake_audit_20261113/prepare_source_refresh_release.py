"""Freeze only metadata-discovery code and thin input pointers; never launch."""
import argparse,hashlib,json,shutil,tarfile
from pathlib import Path

OWN=Path(__file__).resolve().parent;REPO=OWN.parents[1]


def prepare(output):
    output=Path(output).resolve()
    if not output.is_relative_to(OWN):raise ValueError('Output must be inside the owned experiment')
    output.mkdir(parents=True,exist_ok=False);files={}
    for name in ['refresh_discovery_pool.py','test_refresh_discovery_pool.py','source_refresh_inputs.json','source_refresh_plan.json','source_refresh.sbatch','preserve_bytes.py']:
        shutil.copyfile(OWN/name,output/name);files[name]=hashlib.sha256((output/name).read_bytes()).hexdigest()
    for name in ['src/genomics_discovery/illumina/sources.py','scripts/sherlock/environment.sh']:
        path=output/'runtime'/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(REPO/name,path);files['runtime/'+name]=hashlib.sha256(path.read_bytes()).hexdigest()
    for package in ['genomics_discovery','genomics_discovery/illumina']:
        name='runtime/src/'+package+'/__init__.py';(output/name).write_text('"""Frozen metadata-only support snapshot."""\n');files[name]=hashlib.sha256((output/name).read_bytes()).hexdigest()
    (output/'PAYLOAD_MANIFEST.json').write_text(json.dumps({'format_version':'source_refresh_payload/1','files':files,'model_calls':0,'fulltext_requests':0,'sequence':['metadata_repair_and_probe','full_metadata_expand'],'original_pool_copied':False},indent=2)+'\n')
    archive=output.with_suffix('.tar.gz')
    with tarfile.open(archive,'x:gz') as handle:handle.add(output,arcname=output.name)
    print(json.dumps({'archive':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();prepare(args.output)
