"""A deliberately building-specific adapter; never edits the frozen OSM observations."""
import argparse, copy, hashlib, json
from pathlib import Path

PARTS = set(range(1283005798,1283005805))
OUTLINE = 130147025
RELATION = 17594944

def apply(raw):
    result = copy.deepcopy(raw)
    unique = {}
    for element in result['elements']:
        key = (element['type'], element['id'])
        if key in unique and unique[key] != element:
            raise ValueError(f'Conflicting observations for {key}')
        unique[key] = element
    result['elements'] = list(unique.values())
    for e in result['elements']:
        if e['type']=='relation' and e['id']==RELATION:
            for m in e['members']:
                if m['ref']==OUTLINE: m['role']='outline'
                elif m['ref'] in PARTS: m['role']='part'
        if e['type']=='way' and e['id'] in PARTS:
            # Exporter hint only: warm sandstone approximates documented buff limestone.
            e['tags']['building:material']='sandstone'
        if e['type']=='way' and e['id']==1283005804:
            # Proposed assignment of the parent's total height to its highest mapped part.
            # Still inferred: roof height and independent survey validation are missing.
            e['tags']['height']='55.4'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    if args.output.exists(): raise FileExistsError(args.output)
    raw=args.source.read_bytes()
    args.output.write_text(json.dumps(apply(json.loads(raw))))
    report={'scope':'Chicago Water Tower only','source_sha256':hashlib.sha256(raw).hexdigest(),
            'changes':[{'attribute':'relation roles','state':'inferred normalization',
                        'reason':'outline has building tag; the seven components have building:part tags'},
                       {'attribute':'renderer wall material','state':'artistic approximation',
                        'value':'sandstone blocks for buff limestone; not a claim that the building is sandstone',
                        'source':'https://www.loc.gov/item/il0097/'},
                       {'attribute':'highest component total height','state':'inferred',
                        'value_m':55.4,'source':'OSM parent way 130147025',
                        'limitation':'Assignment and added roof height need independent validation'}],
            'not_changed':['source file','footprint coordinates','terrain','other buildings'],
            'not_implemented':['AI','verified facade layout','independent geometry scoring']}
    args.output.with_suffix('.overlay.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
