"""Register a proof-bound building stage for the live publisher."""
import argparse
import json
from pathlib import Path

from building_delivery import make_request


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--exchange', type=Path, required=True)
    parser.add_argument('--native-proof', type=Path, required=True)
    parser.add_argument('--closed-proof', type=Path, required=True)
    args = parser.parse_args()
    path, request = make_request(args.stage, args.exchange, args.native_proof, args.closed_proof)
    print(json.dumps({'request': str(path), 'stage_manifest_sha256': request['stage_manifest_sha256'],
                      'patches': request['patches'], 'changed_cells': request['changed_cells'],
                      'llm_used': False}, indent=2))
