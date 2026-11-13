"""A Qiu bootstrap default must not replace the explicitly selected frozen run."""
import os
from pathlib import Path
import shlex
import subprocess


def test_explicit_runtime_and_run_survive_qiu_bootstrap(tmp_path):
    script=Path('scripts/sherlock/pipeline.sbatch').read_text()
    prefix=script.split('export PYTHONPATH=',1)[0]
    prefix='\n'.join(line for line in prefix.splitlines() if not line.startswith(('hostname','printenv','cat /etc/agents/')))
    bootstrap=tmp_path/'bootstrap.sh';bootstrap.write_text('export ATLAS_REPOSITORY=/wrong/default\nexport ATLAS_RUN_DIR=/wrong/run\nexport ATLAS_ARCHIVE_ROOT=/wrong/archive\n')
    prefix=prefix.replace('source /home/groups/xiaojie/mstojkov/atlas-qiu-environment.sh','source '+shlex.quote(str(bootstrap)))
    env={**os.environ,'ATLAS_REPOSITORY':str(tmp_path/'frozen runtime'),'ATLAS_RUN_DIR':str(tmp_path/'recorded run'),
         'ATLAS_ARCHIVE_ROOT':str(tmp_path/'durable'),'GROUP_HOME':str(tmp_path/'group')}
    helpers=Path(env['ATLAS_REPOSITORY'])/'scripts/sherlock';helpers.mkdir(parents=True)
    (helpers/'environment.sh').write_text(':\n');(helpers/'atlas-account.sh').write_text(':\n')
    result=subprocess.run(['bash','-c',prefix+'\nprintf "%s\\n" "$ATLAS_REPOSITORY" "$ATLAS_RUN_DIR" "$ATLAS_ARCHIVE_ROOT" "$ATLAS_CODEX_BIN_DIR"'],env=env,text=True,capture_output=True,check=True)
    assert result.stdout.splitlines()==[env['ATLAS_REPOSITORY'],env['ATLAS_RUN_DIR'],env['ATLAS_ARCHIVE_ROOT'],str(tmp_path/'group/mstojkov/software/codex/0.163.0/bin')]
    (helpers/'environment.sh').write_text('export ATLAS_RUN_DIR=/wrong/post-helper\n')
    changed=subprocess.run(['bash','-c',prefix],env=env,text=True,capture_output=True)
    assert changed.returncode==2;assert 'changed the selected run' in changed.stderr
    (helpers/'environment.sh').write_text('export ATLAS_REPOSITORY=/wrong/post-helper\n')
    changed=subprocess.run(['bash','-c',prefix],env=env,text=True,capture_output=True)
    assert changed.returncode==2;assert 'changed the selected runtime' in changed.stderr
