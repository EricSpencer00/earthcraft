import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import nbtlib as n
import numpy as np
from shapely.geometry import box,mapping

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from facade_importer import compile_packets, registration_check, stage, verify_source_files
from facade_pilot import canonical_sha
from facade_world import BlockReader
from apply_building_stage_closed import mutate_chunk
from metric_world import packed

FRAME={'crs':'+proj=tmerc +lat_0=41.8972 +lon_0=-87.62443 +k=1 +datum=WGS84 +units=m',
       'west':-32,'north':33,'vertical_offset_m':-116,'dimension_min_y':-64,'dimension_height':1024}


def fixture():
    buildings=[{'pilot_id':f'test-{i:02d}','footprint_geometry':mapping(box(-33,31,-31,33))} for i in range(20)]
    pilot={'frame':FRAME,'building_count':20,'buildings':buildings,'selection_sha256':canonical_sha(buildings)}
    sources=[{'image_id':str(i),'photo_sha256':'a'*64,'source_metadata_sha256':'b'*64,
              'captured_at':1000,'creator':'synthetic fixture','license':'test only',
              'attribution':'synthetic fixture, not street-photo evidence','camera_world_xyz':[i*3,70,0]} for i in range(4)]
    candidate={'schema':'earthcraft.registered-facade-observations.v1','binding_frame':'fixture',
        'sources':sources,'observations':[{'building_id':'test-00','image_id':'0','face':'east',
            'observed':True,'static_surface':True,'depth_residual_m':.01,'facing_cosine':1,
            'source_xyz':[-.5,65.5,1.5],'rgb':[180,20,20]}]}
    controls=[{'image_id':str(2+i%2),'source_xyz':[i%3,i//3+70,0],
               'measured_world_xyz':[i%3,i//3+70,0]} for i in range(6)]
    receipt={'candidate_sha256':canonical_sha(candidate),'frame_sha256':canonical_sha(FRAME),
        'world_from_source':np.eye(4).tolist(),'fit_image_ids':['0','1'],'heldout_controls':controls}
    return pilot,candidate,receipt


class FakeReader:
    def __init__(self):
        self.value={'Name':'minecraft:gray_concrete'};self.neighbor='minecraft:air';self.entity=False
    def state(self,cell):return self.value
    def name(self,cell):return self.neighbor
    def has_entity(self,cell):return self.entity


class FacadeImporterTests(unittest.TestCase):
    def compile(self,pilot=None,candidate=None,receipt=None,reader=None,protected=()):
        p,c,r=fixture()
        if candidate is not None:
            c=candidate;r['candidate_sha256']=canonical_sha(c)
        return compile_packets(pilot or p,c,receipt or r,reader or FakeReader(),
            {'minecraft:gray_concrete':[80,80,80],'minecraft:red_concrete':[180,20,20]},protected)

    def test_existing_negative_coordinate_wall_uses_shared_codec_and_replays(self):
        packets,report=self.compile();p=packets[0]
        self.assertEqual((p['cx'],p['cz']),(-1,0));self.assertEqual(p['cells'],1)
        self.assertEqual(report['proposed_color_cells'],1);self.assertEqual(report['new_face_colors_applied'],0)
        values=np.zeros(4096,int);values[1*256+1*16+15]=1
        tag=n.Compound({'xPos':n.Int(-1),'zPos':n.Int(0),'sections':n.List[n.Compound]([
            n.Compound({'Y':n.Byte(4),'block_states':n.Compound({'palette':n.List[n.Compound]([
                n.Compound({'Name':n.String('minecraft:air')}),n.Compound({'Name':n.String('minecraft:gray_concrete')})]),
                'data':packed(values,4)})})])})
        changed,counts=mutate_chunk(tag,p);self.assertEqual(counts['written'],1)
        _,replay=mutate_chunk(changed,p);self.assertEqual(replay['already_target'],1)
        edited=copy.deepcopy(tag);edited['sections'][0]['block_states']['palette'][1]['Name']=n.String('minecraft:diamond_block')
        _,conflict=mutate_chunk(edited,p);self.assertEqual(conflict['conflicts'],1)
        self.assertEqual(conflict['written'],0)

    def test_unregistered_candidates_are_rejected(self):
        p,c,r=fixture();c['schema']='earthcraft.street-photo-candidates.v1'
        with self.assertRaisesRegex(ValueError,'registered facade'):self.compile(candidate=c)

    def test_changed_frame_selection_and_candidate_are_rejected(self):
        p,c,r=fixture();r['candidate_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'bound'):self.compile(receipt=r)
        p['buildings'][0]['pilot_id']='changed'
        with self.assertRaisesRegex(ValueError,'selection'):self.compile(pilot=p)

    def test_training_images_cannot_be_held_out(self):
        p,c,r=fixture();r['heldout_controls'][0]['image_id']='0'
        with self.assertRaisesRegex(ValueError,'held-out'):self.compile(receipt=r)

    def test_registration_error_scale_and_control_duplicates_are_rejected(self):
        p,c,r=fixture();r['heldout_controls'][0]['measured_world_xyz'][0]+=10
        with self.assertRaisesRegex(ValueError,'tolerance'):self.compile(receipt=r)
        p,c,r=fixture();r['world_from_source'][0][0]=2
        with self.assertRaisesRegex(ValueError,'metre'):self.compile(receipt=r)
        p,c,r=fixture()
        for control in r['heldout_controls']:control['source_xyz']=[0,70,0];control['measured_world_xyz']=[0,70,0]
        with self.assertRaisesRegex(ValueError,'distinct'):self.compile(receipt=r)

    def test_unknown_occluded_or_moving_observations_do_not_fill_walls(self):
        for key,value in [('observed',False),('static_surface',False),('depth_residual_m',.2),('facing_cosine',.1)]:
            p,c,r=fixture();c['observations'][0][key]=value
            packets,report=self.compile(candidate=c)
            self.assertEqual(packets,[]);self.assertEqual(sum(report['rejected_observations'].values()),1)

    def test_missing_neighbor_air_geometry_properties_and_entities_are_preserved(self):
        for field,value in [('neighbor',None),('neighbor','minecraft:stone'),('value',{'Name':'minecraft:air'}),
                            ('value',{'Name':'minecraft:gray_concrete','Properties':{'test':'changed'}}),('entity',True)]:
            reader=FakeReader();setattr(reader,field,value)
            packets,_=self.compile(reader=reader);self.assertEqual(packets,[])
        self.assertEqual(self.compile(protected=[[-1,65,1]])[0],[])

    def test_conflicting_voxel_face_colors_abstain(self):
        p,c,r=fixture();second=dict(c['observations'][0],face='west',rgb=[80,80,80]);c['observations'].append(second)
        packets,report=self.compile(candidate=c)
        self.assertEqual(packets,[]);self.assertEqual(report['rejected_observations']['conflicting_face_or_building_colors'],2)

    def test_source_hash_format_does_not_substitute_for_retained_bytes(self):
        _,c,_=fixture()
        with tempfile.TemporaryDirectory() as folder:
            for s in c['sources']:s.update(photo_path='missing.jpg',metadata_path='missing.json')
            with self.assertRaisesRegex(ValueError,'Retained source'):verify_source_files(c,folder)
            c['sources'][0]['photo_path']='../outside.jpg'
            with self.assertRaisesRegex(ValueError,'inside'):verify_source_files(c,folder)

    def test_missing_native_region_is_not_air(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(BlockReader(folder).name((-1,65,1)))
