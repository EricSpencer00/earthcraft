"""Bounded acquisition of public HABS Water Tower reference drawings, evaluation only."""
import hashlib
import json
from pathlib import Path
import subprocess
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]


def main():
    out=ROOT/'runs/water-tower-reference'
    out.mkdir(exist_ok=True)
    records=[]
    for number in (1,2,3):
        url=f'https://cdn.loc.gov/master/pnp/habshaer/il/il0000/il0097/sheet/{number:05d}a.tif'
        path=out/f'sheet-{number}.tif'
        if not path.exists():
            partial=path.with_suffix('.part')
            subprocess.run(['curl','--fail','--location','--silent','--show-error',
                '--max-time','30','--max-filesize',str(5*1024**2),url,'--output',str(partial)],check=True)
            with Image.open(partial) as drawing:drawing.verify()
            partial.replace(path)
        with Image.open(path) as drawing:
            drawing.convert('RGB').save(out/f'sheet-{number}.png')
            shape=drawing.size
        records.append({'url':url,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                        'pixels':shape,'bytes':path.stat().st_size})
    report={'collection':'HABS ILL,16-CHIG,43, Library of Congress',
            'catalog':'https://www.loc.gov/item/il0097/',
            'rights':'No known restrictions on US Government images; third-party copied images may be restricted.',
            'rights_source':'https://www.loc.gov/rr/print/res/114_habs.html',
            'role':'Reference evaluation; not input to photo reconstruction or fabricated survey geometry',
            'assets':records}
    (out/'manifest.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
