"""Compute contributions: PR-mode uploads, allowed paths, clean templates, validator verdicts."""
import gzip
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import nbtlib  # noqa: E402

import clean_templates  # noqa: E402
import contribute  # noqa: E402
import contributions  # noqa: E402
import hf_store as hs  # noqa: E402
import validate_contributions as vc  # noqa: E402
from regional_fixtures import FakeHub, REPO, make_ctx  # noqa: E402


class PrHub(FakeHub):
    """FakeHub whose commits can open a pull request (owner is someone else)."""

    def __init__(self):
        super().__init__()
        self.revisions = []

    def whoami(self):
        return 'contributor'

    def commit(self, repo, items, message, revision=None, create_pr=False):
        self.revisions.append((revision, create_pr))
        result = super().commit(repo, items, message, revision=revision)
        if create_pr:
            result.update(pr_num=7, pr_url='https://huggingface.co/datasets/x/discussions/7')
        return result


def region(chunks):
    """Anvil bytes holding {slot: nbt Compound} (zlib)."""
    header, body = bytearray(8192), b''
    for slot, tag in sorted(chunks.items()):
        buffer = io.BytesIO()
        nbtlib.File(tag).write(buffer)
        payload = zlib.compress(buffer.getvalue())
        record = (len(payload) + 1).to_bytes(4, 'big') + b'\x02' + payload
        record += b'\0' * (-len(record) % 4096)
        header[slot * 4:slot * 4 + 3] = (2 + len(body) // 4096).to_bytes(3, 'big')
        header[slot * 4 + 3] = len(record) // 4096
        body += record
    return bytes(header) + body


class ContributionTests(unittest.TestCase):
    def test_pull_request_mode_skips_the_owner_check_and_extends_one_pr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hub = PrHub()
            hub.add_repo(REPO, {})
            ctx = make_ctx(root / 'scratch', hub, batch_files=2)
            items = []
            for index in range(3):
                path = root / f'{index}.json'
                path.write_bytes(b'{}' + bytes([65 + index]))
                digest = hs.hash_file(path)
                items.append({'path': f'tiles/v2/{index}_0/tile.json', 'local': str(path), 'sha256': digest.sha256,
                              'bytes': digest.size})
            with self.assertRaisesRegex(ValueError, 'belong'):
                hs.push_batch(ctx, items, 'x')  # a non-owner cannot commit to main
            result = hs.push_batch(ctx, items, 'Contribute', create_pr=True)
            self.assertEqual(result['pr']['num'], 7)
            self.assertEqual(hub.revisions, [('main', True), ('refs/pr/7', False)])

    def test_allowed_paths_and_diff_parsing(self):
        self.assertEqual(contributions.classify('tiles/v2/-3_4/region/r.-3.4.mca'), ('tile', '-3_4'))
        self.assertEqual(contributions.classify('tiles/v2/-3_4/receipts/appearance.json'), ('tile', '-3_4'))
        self.assertEqual(contributions.classify('contributions/20261017-me--3_4.json'), ('manifest', None))
        for bad in ('tiles/v2/-3_4/region/r.1.1.mca', 'tiles/v2/-3_4/level.dat', 'tiles/v2/-3_4/playerdata/x.dat',
                    'catalog.json', 'contributions/../x.json', 'tiles/v1/x.tar'):
            self.assertIsNone(contributions.classify(bad)[0], bad)
        diff = ('diff --git a/tiles/v2/1_2/tile.json b/tiles/v2/1_2/tile.json\nnew file mode 100644\n+{}\n'
                'diff --git a/catalog.json b/catalog.json\ndeleted file mode 100644\n-{}\n')
        self.assertEqual(contributions.diff_files(diff), {'tiles/v2/1_2/tile.json': 'added', 'catalog.json': 'deleted'})

    def test_chunk_digest_ignores_layout_but_not_content(self):
        a = region({0: nbtlib.Compound({'x': nbtlib.Int(1)}), 5: nbtlib.Compound({'y': nbtlib.Int(2)})})
        b = region({5: nbtlib.Compound({'y': nbtlib.Int(2)}), 0: nbtlib.Compound({'x': nbtlib.Int(1)})})
        c = region({0: nbtlib.Compound({'x': nbtlib.Int(9)}), 5: nbtlib.Compound({'y': nbtlib.Int(2)})})
        self.assertEqual(contributions.chunk_digest(a), contributions.chunk_digest(b))
        self.assertNotEqual(contributions.chunk_digest(a), contributions.chunk_digest(c))

    def test_pick_skips_hub_open_prs_and_other_shards(self):
        plan = {'tiles': [{'id': f'{x}_{z}'} for z in range(8) for x in range(2)]}
        pending = {t['id'] for t in plan['tiles']} - {'1_7'}
        chosen = contribute.pick(plan, 3, on_hub={'0_7'}, in_prs={'1_6': 4}, pending=pending, order='reverse')
        self.assertEqual(chosen, ['0_6', '1_5', '0_5'])
        chosen = contribute.pick(plan, 10, on_hub=set(), in_prs={}, pending=pending, shard=(0, 2), order='plan')
        self.assertEqual({int(t.split('_')[1]) // 4 for t in chosen}, {0})

    def test_clean_template_has_no_player_and_only_the_sample_chunk(self):
        with tempfile.TemporaryDirectory() as directory:
            src, out = Path(directory) / 'private', Path(directory) / 'clean'
            world = src / clean_templates.CHUNK
            (world / 'region').mkdir(parents=True)
            level = nbtlib.File({'Data': nbtlib.Compound({'LevelName': nbtlib.String('t'),
                                                         'Player': nbtlib.Compound({'UUID': nbtlib.IntArray([1, 2, 3, 4])})})},
                                gzipped=True)
            level.save(world / 'level.dat')
            sample = nbtlib.Compound({'DataVersion': nbtlib.Int(4556), 'xPos': nbtlib.Int(0)})
            (world / 'region/r.0.0.mca').write_bytes(region({0: sample, 1: nbtlib.Compound({'xPos': nbtlib.Int(1)})}))
            pack = src / clean_templates.HEIGHT
            (pack / 'data/minecraft/dimension_type').mkdir(parents=True)
            (pack / 'pack.mcmeta').write_text('{}')
            (pack / 'data/minecraft/dimension_type/overworld.json').write_text('{}')
            (pack / 'notes.txt').write_text('not copied')
            manifest = clean_templates.build(src, out)
            self.assertFalse(manifest['player_data'])
            self.assertNotIn('Player', nbtlib.load(out / clean_templates.CHUNK / 'level.dat')['Data'])
            from inspect_world import chunks
            kept = list(chunks(out / clean_templates.CHUNK / 'region/r.0.0.mca'))
            self.assertEqual([(slot, dict(tag)) for slot, tag, _ in kept], [(0, dict(sample))])
            self.assertEqual(len(manifest['files']), 4)
            self.assertFalse((out / clean_templates.HEIGHT / 'notes.txt').exists())

    def test_validator_fails_disallowed_paths_before_reading_content(self):
        reader = SimpleNamespace(info=lambda paths, revision=None: {})
        plan = {'tiles': [{'id': '1_2'}]}
        files = {'tiles/v2/1_2/tile.json': 'added', 'tiles/v2/1_2/region/r.1.2.mca': 'added',
                 'world/meta/level.dat': 'added', 'contributions/a.json': 'added'}
        args = SimpleNamespace(spot_check_rate=0)
        report, tiles = vc.validate(None, reader, REPO, 3, 'abc', files, plan, 'p', args)
        self.assertFalse(report.passed)
        self.assertEqual(tiles, ['1_2'])
        text = report.markdown(3, 'abc' * 10, tiles)
        self.assertIn('**FAIL**', text)
        self.assertIn('world/meta/level.dat', text)
        self.assertIn(f'{contributions.VALIDATOR_MARKER} head={"abc" * 10} verdict=FAIL', text)
        self.assertIn('does not merge', text)
        files = {'tiles/v2/9_9/tile.json': 'added', 'contributions/a.json': 'added'}
        report, _ = vc.validate(None, reader, REPO, 3, 'abc', files, plan, 'p', args)
        self.assertIn(('Tiles are in the plan', False, '9_9'), report.checks)

    def test_hub_lfs_rules_in_gitattributes_are_allowed_only_for_the_requests_tiles(self):
        reader = SimpleNamespace(info=lambda paths, revision=None: {})
        plan = {'tiles': [{'id': '1_2'}]}
        args = SimpleNamespace(spot_check_rate=0)
        files = {'.gitattributes': 'modified', 'tiles/v2/1_2/region/r.1.2.mca': 'added', 'contributions/a.json': 'added'}
        diff = ('diff --git a/.gitattributes b/.gitattributes\n--- a/.gitattributes\n+++ b/.gitattributes\n'
                '@@ -1,1 +1,2 @@\n tiles/v2/0_0/region/r.0.0.mca filter=lfs diff=lfs merge=lfs -text\n'
                '+tiles/v2/1_2/region/r.1.2.mca filter=lfs diff=lfs merge=lfs -text\n')
        report, _ = vc.validate(None, reader, REPO, 3, 'abc', files, plan, 'p', args, contributions.gitattributes_lines(diff))
        self.assertIn(('Only allowed paths', True, '2 files'), report.checks)
        evil = diff + '+*.dat filter=lfs diff=lfs merge=lfs -text\n'
        report, _ = vc.validate(None, reader, REPO, 3, 'abc', files, plan, 'p', args, contributions.gitattributes_lines(evil))
        self.assertFalse(report.checks[0][1])
        commits = SimpleNamespace(events=[SimpleNamespace(type='comment', oid=None), SimpleNamespace(type='commit', oid='c1')])
        self.assertEqual(contributions.pull_request_diff(commits, REPO, fetch=lambda r, b, h, t: f'{b}..{h}'), 'c1~1..c1')

    def test_spot_check_selection_is_deterministic_about_one_in_n(self):
        due = [vc.spot_check_due(pr, 'head', 20) for pr in range(2000)]
        self.assertEqual(due, [vc.spot_check_due(pr, 'head', 20) for pr in range(2000)])
        self.assertTrue(60 < sum(due) < 140)
        self.assertFalse(vc.spot_check_due(1, 'head', 0))


if __name__ == '__main__':
    unittest.main()
