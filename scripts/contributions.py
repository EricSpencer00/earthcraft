"""Shared rules for compute contributions: dataset pull requests that add tiles/v2/<tile>/.

A contribution is one pull request on the Hugging Face dataset holding only
generated tile folders and one manifest under contributions/. The contributor
script (contribute.py) and the owner's validator (validate_contributions.py)
both use these definitions, so what a contributor may send is exactly what the
validator accepts.
"""
import hashlib
import io
import re
import zlib

MANIFEST_SCHEMA = 'earthcraft.contribution.v1'
MANIFEST_DIR = 'contributions'
CLIENT_VERSION = '1.21.10'
# Mojang's published SHA-1 of the official 1.21.10 client.jar (piston-meta version manifest). Block
# colours are read from it, so the same JAR gives byte-identical tiles. It is never redistributed.
CLIENT_SHA1 = 'd3bdf582a7fa723ce199f3665588dcfe6bf9aca8'
VALIDATOR_MARKER = 'earthcraft-validator'

TILE = r'(-?\d+)_(-?\d+)'
# Regions are packed .mcp (scripts/region_pack.py); tiles from before packing carry raw .mca.
TILE_FILE = re.compile(r'tiles/v2/' + TILE + r'/(tile\.json|earthcraft\.json|region/r\.(-?\d+)\.(-?\d+)\.mc[ap]|'
                       r'lq/earthcraft\.json|lq/region/r\.(-?\d+)\.(-?\d+)\.mc[ap]|'
                       r'receipts/(?:sources|geometry|appearance)\.json)')
MANIFEST_FILE = re.compile(MANIFEST_DIR + r'/[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.json')
MAX_REGION_BYTES = 64 * 2**20
REGION_SUFFIXES = ('.mca', '.mcp')
MAX_JSON_BYTES = 4 * 2**20
MAX_TILES = 200
MAX_PR_BYTES = 4 * 2**30
DIFF_HEADER = re.compile(r'^diff --git a/(\S+) b/(\S+)$', re.M)
DELETED = re.compile(r'^deleted file mode', re.M)


def classify(path):
    """('tile', tile_id) | ('manifest', None) | (None, reason) for one changed path."""
    match = TILE_FILE.fullmatch(path)
    if match:
        rx, rz = match.group(1), match.group(2)
        region = [g for g in match.groups()[3:] if g is not None]
        if region and region != [rx, rz]:
            return None, 'region file name does not match its tile folder'
        return 'tile', f'{rx}_{rz}'
    if MANIFEST_FILE.fullmatch(path):
        return 'manifest', None
    return None, 'path outside tiles/v2/<tile>/ and contributions/'


GITATTRIBUTES = '.gitattributes'
LFS_RULE = re.compile(r'(\S+) filter=lfs diff=lfs merge=lfs -text')


def diff_blocks(diff):
    """[(path, block text)] for each file in a git-format diff."""
    starts = [(m.start(), m.group(2)) for m in DIFF_HEADER.finditer(diff or '')]
    return [(path, diff[start:(starts[index + 1][0] if index + 1 < len(starts) else len(diff))])
            for index, (start, path) in enumerate(starts)]


def diff_files(diff):
    """{path: 'added'|'modified'|'deleted'} from a git-format diff."""
    return {path: 'deleted' if DELETED.search(block) else ('added' if 'new file mode' in block else 'modified')
            for path, block in diff_blocks(diff)}


def gitattributes_lines(diff):
    """(added, removed) lines of .gitattributes in a diff; the Hub adds one LFS rule per large file."""
    added, removed = [], []
    for path, block in diff_blocks(diff):
        if path != GITATTRIBUTES:
            continue
        for line in block.splitlines():
            if line.startswith('+') and not line.startswith('+++'):
                added.append(line[1:])
            elif line.startswith('-') and not line.startswith('---'):
                removed.append(line[1:])
    return added, removed


def compare(repo, base, head, token=None, endpoint='https://huggingface.co', limit=64 * 2**20):
    """Raw git diff between two revisions of the dataset (the Hub's compare API)."""
    import urllib.parse
    import urllib.request
    url = f'{endpoint}/api/datasets/{repo}/compare/{urllib.parse.quote(base, safe="~")}..{urllib.parse.quote(head, safe="")}'
    headers = {'User-Agent': 'earthcraft-contributions/1'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Pull request diff exceeds its read bound')
    return raw.decode('utf-8', 'replace')


def pull_request_diff(details, repo, token=None, fetch=compare):
    """The diff of the pull request's own commits (not of main moving on since it branched)."""
    commits = [event.oid for event in details.events if getattr(event, 'type', '') == 'commit' and event.oid]
    return '\n'.join(fetch(repo, f'{oid}~1', oid, token) for oid in commits)


def hub_token():
    from huggingface_hub import get_token
    return get_token()


def open_pull_requests(api, repo):
    return [d for d in api.get_repo_discussions(repo_id=repo, repo_type='dataset', discussion_type='pull_request',
                                                 discussion_status='open')]


def tiles_in_open_pull_requests(api, repo):
    """{tile_id: pr_num} for every tile folder touched by an open dataset pull request."""
    claimed = {}
    for discussion in open_pull_requests(api, repo):
        details = api.get_discussion_details(repo_id=repo, discussion_num=discussion.num, repo_type='dataset')
        for path in diff_files(pull_request_diff(details, repo, hub_token())):
            kind, tile = classify(path)
            if kind == 'tile':
                claimed.setdefault(tile, discussion.num)
    return claimed


def tiles_on_hub(api, repo, revision='main'):
    """Tile ids with a folder under tiles/v2/ on the dataset's main branch."""
    found = set()
    for item in api.list_repo_tree(repo, path_in_repo='tiles/v2', repo_type='dataset', revision=revision):
        name = item.path.rsplit('/', 1)[-1]
        if re.fullmatch(TILE, name):
            found.add(name)
    return found


def chunk_digest(raw):
    """SHA-256 over every chunk's decompressed NBT, slot by slot.

    Two regions with the same chunks compare equal even if a writer laid out
    sectors or compression differently; used by the re-generation spot check.
    """
    digest = hashlib.sha256()
    for slot in range(1024):
        offset = int.from_bytes(raw[slot * 4:slot * 4 + 3], 'big') * 4096
        if not offset:
            continue
        length = int.from_bytes(raw[offset:offset + 4], 'big')
        mode = raw[offset + 4]
        body = raw[offset + 5:offset + 4 + length]
        if mode == 2:
            data = zlib.decompress(body)
        elif mode == 1:
            import gzip
            data = gzip.GzipFile(fileobj=io.BytesIO(body)).read()
        else:
            data = body
        digest.update(slot.to_bytes(2, 'big') + len(data).to_bytes(4, 'big') + data)
    return digest.hexdigest()
