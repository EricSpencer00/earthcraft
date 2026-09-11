"""Local-only continuation: await source preparation, run pilot, then full Chicago draft."""
import argparse,json,subprocess,sys,time
from pathlib import Path
import psutil
from chicago import STATE,write_json

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,required=True)
parser.add_argument('--preparation-pid',type=int,required=True)
args=parser.parse_args()
status={'status':'waiting_for_source_preparation','ai_used':False,'cloud_inference':False}
write_json(STATE/'pipeline-status.json',status)
try:
    preparation=psutil.Process(args.preparation_pid)
    preparation.wait(timeout=1800)
except psutil.NoSuchProcess:
    pass
except psutil.TimeoutExpired:
    status.update(status='stopped',reason='Source preparation exceeded continuation wait limit')
    write_json(STATE/'pipeline-status.json',status);raise SystemExit(1)
if not (STATE/'inventory.json').exists():
    status.update(status='stopped',reason='Source preparation did not produce verified inventory; inspect prepare.log')
    write_json(STATE/'pipeline-status.json',status);raise SystemExit(1)
for stage in ('pilot','generate'):
    status.update(status='running',stage=stage)
    write_json(STATE/'pipeline-status.json',status)
    result=subprocess.run([sys.executable,str(Path(__file__).with_name('chicago.py')),stage,'--root',str(args.root)])
    if result.returncode:
        status.update(status='stopped',reason=f'{stage} failed; inspect stage status/log')
        write_json(STATE/'pipeline-status.json',status);raise SystemExit(result.returncode)
    if stage=='pilot':
        report=json.loads((STATE/'loop-pilot/status.json').read_text())
        if report['peak_rss_bytes']>16*1024**3 or report['elapsed_seconds']>1200:
            status.update(status='stopped',reason='Downtown pilot exceeded full-run promotion resource budget')
            write_json(STATE/'pipeline-status.json',status);raise SystemExit(1)
status.update(status='draft_generated_unverified',stage='done',game_load_verified=False,building_accuracy_verified=False)
write_json(STATE/'pipeline-status.json',status)
