import copy
from pathlib import Path
import sys
import unittest
import tempfile,json
from unittest.mock import patch

import nbtlib as n
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from city_save_update import extend_height,pavement_overlay,generated_chunk_predicate,read_region,publish,stage
from city_assemble import chunk_payload
from metric_world import packed,region_write
from verify_metric_world import unpack
from world_replay import files_snapshot


class SavedCityTests(unittest.TestCase):
    def save_fixture(self,root,name,blocks):
        world=root/name;(world/'region').mkdir(parents=True)
        pack=world/'datapacks/earthcraft_height';pack.mkdir(parents=True);(pack/'pack.mcmeta').write_text('{}')
        report={'source':{'crs':'EPSG:3857','size':32},'dimension_height':128,'vertical_offset_m':0,'spawn':[0,1,0]}
        (world/'earthcraft.json').write_text(json.dumps(report));(world/'block-verification.json').write_text('{}')
        (world/'session.lock').write_bytes(b'lock')
        data=n.Compound({'Player':n.Compound({'Pos':n.List[n.Double]([.5,2,.5])}),'LevelName':n.String(name)})
        for key in ('BorderCenterX','BorderCenterZ','BorderSize','BorderSizeLerpTarget'):data[key]=n.Double(100)
        data['BorderSizeLerpTime']=n.Long(0)
        n.File({'Data':data},gzipped=True).save(world/'level.dat')
        tags=[]
        for x,block in blocks.items():
            tag=n.File({'xPos':n.Int(x),'zPos':n.Int(0),'sections':n.List[n.Compound]([
                n.Compound({'Y':n.Byte(0),'block_states':n.Compound({'palette':n.List[n.Compound]([
                    n.Compound({'Name':n.String('minecraft:'+block)})])})})])})
            tags.append((x,0,tag))
        region_write(world/'region/r.0.0.mca',tags)
        return world

    def test_streaming_retains_cleared_chunks_and_excludes_external_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);current=self.save_fixture(root,'current',{0:'stone',1:'air'})
            baseline=self.save_fixture(root,'baseline',{0:'stone'})
            generated=self.save_fixture(root,'generated',{0:'dirt',1:'dirt',2:'grass_block'})
            (generated/'datapacks/earthcraft_height/._bad.json').write_bytes(b'Mac metadata')
            before=files_snapshot(current);out=root/'staged'
            result=stage(current,generated,baseline,out)
            actual=read_region(out/'region/r.0.0.mca')
            self.assertEqual(str(actual[(0,0)]['sections'][0]['block_states']['palette'][0]['Name']),'minecraft:stone')
            self.assertFalse(chunk_payload(actual[(1,0)]))
            self.assertEqual(str(actual[(2,0)]['sections'][0]['block_states']['palette'][0]['Name']),'minecraft:grass_block')
            self.assertEqual(result['new_city_chunks'],1);self.assertEqual(result['peak_merged_region_chunks'],3)
            self.assertFalse((out/'datapacks/earthcraft_height/._bad.json').exists())
            self.assertEqual(files_snapshot(current),before)

    def test_changing_generated_city_cannot_produce_a_publishable_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);current=self.save_fixture(root,'current',{0:'stone'})
            baseline=self.save_fixture(root,'baseline',{0:'stone'})
            generated=self.save_fixture(root,'generated',{0:'stone',1:'dirt'})
            before=files_snapshot(current);out=root/'staged'
            def changing_source(path,records):
                region_write(path,records);(generated/'changed-during-stage.json').write_text('{}')
            with patch('city_save_update.region_write',side_effect=changing_source):
                with self.assertRaisesRegex(ValueError,'Generated city changed'):stage(current,generated,baseline,out)
            self.assertFalse((out/'city-update-receipt.json').exists());self.assertEqual(files_snapshot(current),before)

    def test_promotion_rejects_changes_after_game_check_without_touching_save(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);current=root/'current';staged=root/'staged';backup=root/'backup'
            current.mkdir();staged.mkdir();(current/'session.lock').write_bytes(b'lock')
            before=files_snapshot(current)
            (staged/'city-update-receipt.json').write_text(json.dumps({'original_snapshot':before}))
            (staged/'geographic-payload.bin').write_bytes(b'verified')
            (staged/'server-verification.json').write_text(json.dumps({'server_load_save_reload_verified':True,
                'verified_input_snapshot':files_snapshot(staged)}))
            (staged/'geographic-payload.bin').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'exact world verified'):publish(current,staged,backup)
            self.assertEqual(files_snapshot(current),before);self.assertFalse(backup.exists())

    def test_cleared_city_chunks_remain_edits_not_new_generation_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            report={'world_offset_xz':[-256,-256],'source':{'size':512}}
            was_generated=generated_chunk_predicate(root,report)
            self.assertTrue(was_generated((-16,-16)));self.assertTrue(was_generated((15,15)))
            self.assertFalse(was_generated((16,0)))
            (root/'city-coverage.json').write_text(json.dumps({'tiles':{'a':{'world_offset_xz':[-256,256],'size_m':256}}}))
            was_generated=generated_chunk_predicate(root,report)
            self.assertTrue(was_generated((-1,31)));self.assertFalse(was_generated((0,31)))
            self.assertFalse(was_generated((-1,15)))

    def test_region_filtered_paint_does_not_claim_other_region_cells(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'ground-appearance.json').write_text(json.dumps({'geometry_changed':False,'llm_used':False,'palette':['gray_concrete','white_concrete']}))
            (root/'earthcraft.json').write_text('{"world_offset_xz":[512,0]}')
            np.save(root/'ground-appearance-blocks.npy',np.ones((16,16),np.uint8))
            np.save(root/'top-heights.npy',np.zeros((16,16),int))
            self.assertEqual(pavement_overlay({},root,region=(0,0))['edited_or_nonpavement_cells_retained'],0)
            self.assertEqual(pavement_overlay({},root,region=(1,0))['edited_or_nonpavement_cells_retained'],256)

    def test_pavement_overlay_preserves_a_player_block_in_the_same_chunk(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'ground-appearance.json').write_text(json.dumps({'geometry_changed':False,'llm_used':False,'palette':['gray_concrete','white_concrete']}))
            (root/'earthcraft.json').write_text('{"world_offset_xz":[0,0]}')
            codes=np.zeros((16,16),np.uint8);codes[0,0]=codes[0,1]=1
            np.save(root/'ground-appearance-blocks.npy',codes);np.save(root/'top-heights.npy',np.zeros((16,16),int))
            values=np.zeros(4096,int);values[1]=1
            tag=n.Compound({'sections':n.List[n.Compound]([n.Compound({'Y':n.Byte(0),'block_states':n.Compound({
                'palette':n.List[n.Compound]([n.Compound({'Name':n.String('minecraft:gray_concrete')}),n.Compound({'Name':n.String('minecraft:diamond_block')})]),
                'data':packed(values,4)})})])})
            result=pavement_overlay({(0,0):tag},root)
            self.assertEqual(result['applied_cells'],1);self.assertEqual(result['edited_or_nonpavement_cells_retained'],1)
            state=tag['sections'][0]['block_states'];decoded=unpack(state['data'],4,4096)
            self.assertEqual(str(state['palette'][int(decoded[0])]['Name']),'minecraft:white_concrete')
            self.assertEqual(str(state['palette'][int(decoded[1])]['Name']),'minecraft:diamond_block')

    def test_added_air_and_lighting_sections_are_not_player_edits(self):
        a={'sections':[{'Y':0,'block_states':{'palette':[{'Name':'minecraft:stone'}]}}]}
        b=copy.deepcopy(a)
        b['sections'] += [{'Y':20,'block_states':{'palette':[{'Name':'minecraft:air'}]}}, {'Y':-5,'SkyLight':[1]}]
        self.assertEqual(chunk_payload(a),chunk_payload(b))
        b['sections'][0]['block_states']['palette'][0]['Name']='minecraft:diamond_block'
        self.assertNotEqual(chunk_payload(a),chunk_payload(b))

    def test_height_extension_preserves_blocks_and_repacks_saved_heightmaps(self):
        values=np.arange(256)%192
        tag=n.Compound({'sections':n.List[n.Compound]([
            n.Compound({'Y':n.Byte(0),'block_states':n.Compound({'palette':n.List[n.Compound]([
                n.Compound({'Name':n.String('minecraft:chest'),'Properties':n.Compound({'facing':n.String('east')})})])}),
                'SkyLight':n.ByteArray([0]*2048)})]),
            'Heightmaps':n.Compound({'WORLD_SURFACE':packed(values,192 .bit_length())}), 'isLightOn':n.Byte(1)})
        before=copy.deepcopy(tag);result=extend_height(tag,192,1024)
        self.assertEqual(tag,before)
        self.assertEqual(chunk_payload(result),chunk_payload(tag))
        np.testing.assert_array_equal(unpack(result['Heightmaps']['WORLD_SURFACE'],1024 .bit_length(),256),values)
        self.assertNotIn('SkyLight',result['sections'][0]);self.assertFalse(result['isLightOn'])


if __name__=='__main__':unittest.main()
