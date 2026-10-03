import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

import nbtlib as n

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from inspect_world import chunks
from photo_display_dedup import analyze,prepare,verify,preview_chunk,restore_preview,tag_sha,entity_uuid,keeper_preference,expected_panels
from playable_acceptance_snapshot import sha


def display(model,identity):
    return n.Compound({'id':n.String('minecraft:item_display'),
        'UUID':n.IntArray([0,0,0,identity]),'Pos':n.List[n.Double]([1.5,65.5,1.5]),
        'Rotation':n.List[n.Float]([180,0]),'Tags':n.List[n.String](['earthcraft_photo_skin']),
        'item':n.Compound({'id':n.String('minecraft:stone'),'count':n.Int(1),
            'components':n.Compound({'minecraft:item_model':n.String(f'earthcraft_skin:face_{model}')})}),
        'transformation':n.Compound({'scale':n.List[n.Float]([1,1,1])}),
        'Air':n.Short(300),'CustomData':n.ByteArray([1,2,3])})


def write_region(path,entities):
    tag=n.File({'DataVersion':n.Int(4671),'Position':n.IntArray([0,0]),
        'Entities':n.List[n.Compound](entities),'UnrelatedChunkData':n.String('preserve exactly')})
    stream=io.BytesIO();tag.write(stream);raw=zlib.compress(stream.getvalue())
    sector=(len(raw)+5+4095)//4096
    header=bytearray(8192);header[0:4]=(2).to_bytes(3,'big')+bytes([sector])
    header[4096:4100]=(1700000000).to_bytes(4,'big')
    body=(len(raw)+1).to_bytes(4,'big')+b'\x02'+raw
    path.write_bytes(header+body+bytes(sector*4096-len(body)))


def fixture(root,panels=2):
    world=root/'synthetic-fixture';world.mkdir();(world/'entities').mkdir();(world/'data').mkdir()
    (world/'session.lock').write_bytes(bytes(3))
    n.File({'Data':n.Compound({'LevelName':n.String('Synthetic regression fixture'),
        'Player':n.Compound({'Inventory':n.List[n.Compound]([n.Compound({'id':n.String('minecraft:diamond')})])})})},gzipped=True).save(world/'level.dat')
    records=[{'id':i,'face':'east' if i==0 else 'north','cell':[1,65,1]} for i in range(panels)]
    with zipfile.ZipFile(world/'resources.zip','w') as archive:
        archive.writestr('earthcraft-skin-manifest.json',json.dumps(records))
    (world/'photo-skin.json').write_text(json.dumps({'display_entities':panels}))
    for name in ('earthcraft.json','city-coverage.json','regional-quality.json'):(world/name).write_text(json.dumps({'retain':'source edits and receipts'}))
    (world/'region').mkdir();(world/'region/r.0.0.mca').write_bytes(b'unchanged-terrain')
    entities=[display(i,i+1) for i in range(panels)]+[display(i,i+1+panels) for i in range(panels)]
    entities.append(n.Compound({'id':n.String('minecraft:armor_stand'),'UUID':n.IntArray([0,0,0,100]),
        'CustomName':n.String('User edited entity'),'Pos':n.List[n.Double]([2,65,2])}))
    write_region(world/'entities/r.0.0.mca',entities)
    return world,entities


def snapshot(world):return {str(p.relative_to(world)):sha(p) for p in world.rglob('*') if p.is_file()}


class PhotoDedupTests(unittest.TestCase):
    def test_exact_duplicate_plan_keeps_different_face_models_at_same_position(self):
        with tempfile.TemporaryDirectory() as folder:
            world,entities=fixture(Path(folder));inventory,plan=analyze(world)
            self.assertEqual(inventory['tagged_photo_displays'],4)
            self.assertEqual(inventory['unique_declared_positions'],1)
            self.assertEqual(plan['planned_after_photo_displays'],2)
            self.assertEqual([o['entity_index'] for o in plan['operations']],[2,3])
            self.assertEqual(plan['world_entities_deleted'],0)
            self.assertEqual(inventory['other_entity_types'],{'minecraft:armor_stand':1})

    def test_in_memory_preview_preserves_user_entity_and_is_exactly_reversible(self):
        with tempfile.TemporaryDirectory() as folder:
            world,entities=fixture(Path(folder));_,plan=analyze(world)
            tag=next(chunks(world/'entities/r.0.0.mca'))[1];original_hash=tag_sha(tag)
            preview=preview_chunk(tag,plan['operations'])
            self.assertEqual(len(preview['Entities']),3)
            self.assertEqual(preview['Entities'][-1],entities[-1])
            self.assertEqual(tag_sha(restore_preview(preview,tag,plan['operations'])),original_hash)
            self.assertEqual(tag_sha(tag),original_hash)

    def test_prepare_writes_backup_and_plan_only_and_all_source_bytes_stay_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);world,_=fixture(root);before=snapshot(world)
            proof=prepare(world,root/'preparation')
            self.assertEqual(before,snapshot(world));self.assertFalse(proof['applied'])
            self.assertEqual(sha(root/'preparation/backup/entities/r.0.0.mca'),sha(world/'entities/r.0.0.mca'))
            self.assertTrue(verify(root/'preparation',world)['owner_guards_match'])
            plan=json.loads((root/'preparation/patch.json').read_text())
            self.assertTrue(plan['dry_run']['exact_nbt_reversal_verified'])
            self.assertFalse(plan['dry_run']['replacement_region_files_created'])
            self.assertEqual(list((root/'preparation').rglob('*.mca')),[root/'preparation/backup/entities/r.0.0.mca'])

    def test_user_edit_to_duplicate_payload_blocks_all_proposed_removals(self):
        with tempfile.TemporaryDirectory() as folder:
            world,entities=fixture(Path(folder));entities[2]['CustomName']=n.String('Intentional user variant')
            write_region(world/'entities/r.0.0.mca',entities)
            inventory,plan=analyze(world)
            self.assertEqual(plan['status'],'blocked');self.assertEqual(plan['operations'],[])
            self.assertTrue(any('beyond UUID' in c['reason'] for c in inventory['conflicts']))

    def test_typed_payload_differences_are_not_discarded(self):
        with tempfile.TemporaryDirectory() as folder:
            world,entities=fixture(Path(folder));entities[2]['Air']=n.Int(300)
            write_region(world/'entities/r.0.0.mca',entities)
            self.assertEqual(analyze(world)[1]['status'],'blocked')

    def test_unknown_panel_or_changed_anchor_blocks_the_plan(self):
        for field in ('unknown_model','position'):
            with tempfile.TemporaryDirectory() as folder:
                world,entities=fixture(Path(folder))
                if field=='unknown_model':entities[0]['item']['components']['minecraft:item_model']=n.String('user:edited_model')
                else:entities[0]['Pos'][0]=n.Double(2.5)
                write_region(world/'entities/r.0.0.mca',entities)
                self.assertEqual(analyze(world)[1]['operations'],[])

    def test_uuid_referenced_in_other_saved_nbt_is_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            world,entities=fixture(Path(folder));identity=entity_uuid(entities[2])
            n.File({'reference':n.String(identity)},gzipped=True).save(world/'data/scoreboard.dat')
            _,plan=analyze(world)
            op=next(o for o in plan['operations'] if o['model']=='earthcraft_skin:face_0')
            self.assertEqual(op['keep_uuid'],identity);self.assertEqual(op['entity_index'],0)

    def test_historical_uuid_preference_preserves_original_identities(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);world,entities=fixture(root)
            preferred={'earthcraft_skin:face_0':entity_uuid(entities[2]),'earthcraft_skin:face_1':entity_uuid(entities[3])}
            _,plan=analyze(world,preferred)
            self.assertEqual([o['entity_index'] for o in plan['operations']],[0,1])
            self.assertTrue(all(o['keep_reason']=='matching_historical_uuid' for o in plan['operations']))
            n.File({'reference':n.String(entity_uuid(entities[0]))},gzipped=True).save(world/'data/scoreboard.dat')
            _,plan=analyze(world,preferred)
            self.assertEqual(next(o for o in plan['operations'] if o['model']=='earthcraft_skin:face_0')['keep_uuid'],entity_uuid(entities[0]))

    def test_keeper_baseline_must_be_unique_complete_and_anchor_matched(self):
        with tempfile.TemporaryDirectory() as folder:
            world,entities=fixture(Path(folder))
            with self.assertRaisesRegex(ValueError,'not unique'):keeper_preference(world,expected_panels(world))
            write_region(world/'entities/r.0.0.mca',entities[:2]+entities[-1:])
            preferred,provenance=keeper_preference(world,expected_panels(world))
            self.assertEqual(len(preferred),2);self.assertEqual(provenance['matched_panels'],2)
            with self.assertRaisesRegex(ValueError,'other panel'):keeper_preference(world,{'other:model':[1,2,3]})

    def test_multiple_saved_references_block_the_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            world,entities=fixture(Path(folder))
            n.File({'references':n.List[n.String]([entity_uuid(entities[0]),entity_uuid(entities[2])])},gzipped=True).save(world/'data/scoreboard.dat')
            self.assertEqual(analyze(world)[1]['status'],'blocked')

    def test_nested_entity_uuid_reference_is_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            world,entities=fixture(Path(folder));entities[-1]['Owner']=copy.deepcopy(entities[3]['UUID'])
            write_region(world/'entities/r.0.0.mca',entities)
            _,plan=analyze(world)
            self.assertEqual(next(o for o in plan['operations'] if o['model']=='earthcraft_skin:face_1')['keep_uuid'],entity_uuid(entities[3]))

    def test_in_memory_preview_rejects_changed_keeper_and_removed_record(self):
        with tempfile.TemporaryDirectory() as folder:
            world,_=fixture(Path(folder));_,plan=analyze(world);tag=next(chunks(world/'entities/r.0.0.mca'))[1]
            for ordinal in (0,2):
                changed=copy.deepcopy(tag);changed['Entities'][ordinal]['CustomName']=n.String('Changed after plan')
                with self.assertRaisesRegex(ValueError,'precondition'):preview_chunk(changed,plan['operations'])

    def test_backup_tampering_or_owner_drift_cannot_pass_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);world,_=fixture(root);prepare(world,root/'preparation')
            (world/'regional-quality.json').write_text('{"newer_receipt":true}')
            with self.assertRaisesRegex(ValueError,'Owner save changed'):verify(root/'preparation',world)
            self.assertTrue(verify(root/'preparation')['backup_verified'])
            (root/'preparation/backup/entities/r.0.0.mca').write_bytes(b'tampered backup')
            with self.assertRaisesRegex(ValueError,'checksum'):verify(root/'preparation')

    def test_existing_and_inside_save_outputs_and_busy_save_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);world,_=fixture(root);output=root/'existing';output.mkdir()
            with self.assertRaises(FileExistsError):prepare(world,output)
            with self.assertRaisesRegex(ValueError,'outside'):prepare(world,world/'output')
            with patch('photo_display_dedup.fcntl.lockf',side_effect=BlockingIOError('busy')):
                with self.assertRaises(BlockingIOError):prepare(world,root/'busy')
            self.assertFalse((root/'busy').exists())

    def test_uniquely_saved_panels_are_a_noop_and_missing_entities_field_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            world,entities=fixture(Path(folder));write_region(world/'entities/r.0.0.mca',entities[:2]+entities[-1:])
            _,plan=analyze(world);self.assertEqual(plan['status'],'already_unique');self.assertEqual(plan['operations'],[])
            empty=n.Compound({'DataVersion':n.Int(1)})
            self.assertEqual(restore_preview(copy.deepcopy(empty),empty,[]),empty)

    def test_symlinked_entity_region_cannot_expand_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);world,_=fixture(root)
            p=world/'entities/r.0.0.mca';p.rename(root/'outside.mca');p.symlink_to(root/'outside.mca')
            with self.assertRaisesRegex(ValueError,'inside the bound save'):analyze(world)
