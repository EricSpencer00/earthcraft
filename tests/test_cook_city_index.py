"""Survey/archive joins retain gaps and reject conflicting source versions."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from shapely.geometry import box,mapping
from shapely.ops import transform
from pyproj import Transformer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from chicago_tiles import digest
from cook_city_index import attach_members,tile_sources,archive_index,survey_index,municipal_coverage,METADATA


def asset(name,crc='00000001',url='source-a'):
    return {'id':name,'member':name+'.las','url':url,'crc32':crc,
            'compressed_bytes':100,'uncompressed_bytes':200}


class CookCityIndexTests(unittest.TestCase):
    def test_missing_outside_source_does_not_abort_city_or_claim_acquisition(self):
        rows=[{'id':name,'native_geometry':box(i*10,0,i*10+10,10)} for i,name in enumerate(['00000001','00000002'])]
        joined=attach_members(rows,[{'members':[asset('00000001')]}])
        self.assertEqual(joined[0]['asset']['id'],'00000001')
        self.assertIsNone(joined[1]['asset'])

    def test_duplicate_bytes_reused_but_conflicting_versions_rejected(self):
        survey=[{'id':'00000001','native_geometry':box(0,0,1,1)}]
        same=[asset('00000001',url='z'),asset('00000001',url='a')]
        self.assertEqual(attach_members(survey,[{'members':same}])[0]['asset']['url'],'a')
        same[1]['crc32']='00000002'
        with self.assertRaises(ValueError):attach_members(survey,[{'members':same}])

    def test_tiled_spatial_join_reports_actual_downloadable_gap(self):
        crs='+proj=tmerc +lat_0=41.8972 +lon_0=-87.62443 +k=1 +ellps=WGS84 +units=m +type=crs'
        inverse=Transformer.from_crs(crs,6455,always_xy=True)
        frame={'crs':crs,'west':0,'north':0,'vertical_offset_m':-116,'dimension_height':1024,'dimension_min_y':-64}
        plan={'frame':frame,'tiles':[{'id':'0_0','west':0,'north':0,'size':16,'source_bounds':[0,-16,16,0]}]}
        survey=[{'id':'00000001','native_geometry':transform(inverse.transform,box(0,-16,8,0)),'asset':asset('00000001')},
                {'id':'00000002','native_geometry':transform(inverse.transform,box(8,-16,16,0)),'asset':None}]
        result=tile_sources(plan,survey)
        self.assertEqual(result['unique_source_tiles'],1)
        self.assertAlmostEqual(result['jobs'][0]['core_indexed_fraction'],1,places=6)
        self.assertAlmostEqual(result['jobs'][0]['core_downloadable_fraction'],.5,places=6)
        self.assertEqual(result['missing_relevant_archive_members'],['00000002'])
        self.assertEqual(result['compressed_source_bytes'],100)
        self.assertFalse(result['source_data_downloaded'])
        self.assertFalse(result['playable_city'])

    def test_offline_metadata_replay_checks_original_range_and_member_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);part=root/'cook-las1';part.mkdir()
            raw=b'original ZIP directory bytes';(part/'range-00.bin').write_bytes(raw)
            members=[asset('00000001')]
            receipt={'members':members,'index_sha256':digest(members),
                'ranges':[{'file':'range-00.bin','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}]}
            (part/'index.json').write_text(json.dumps(receipt))
            self.assertEqual(archive_index(1,root),receipt)
            (part/'range-00.bin').write_bytes(b'changed')
            with self.assertRaises(ValueError):archive_index(1,root)

    def test_municipal_coverage_separates_boundary_context_and_missing_city_source(self):
        crs='+proj=tmerc +lat_0=41.8972 +lon_0=-87.62443 +k=1 +ellps=WGS84 +units=m +type=crs'
        native=Transformer.from_crs(crs,6455,always_xy=True)
        geography=Transformer.from_crs(crs,4326,always_xy=True)
        document={'type':'FeatureCollection','features':[{'type':'Feature','properties':{},
            'geometry':mapping(transform(geography.transform,box(0,0,16,16)))}]}
        plan={'frame':{'crs':crs},'boundary_geometry_sha256':digest(document)}
        survey=[{'native_geometry':transform(native.transform,box(-16,-16,8,32)),'asset':asset('00000001')},
                {'native_geometry':transform(native.transform,box(8,-16,32,32)),'asset':None}]
        result=municipal_coverage(plan,document,survey)
        # Two CRS round trips perturb edges at sub-micrometre scale; this is
        # numerical tolerance, not a relaxation of real-world source accuracy.
        self.assertAlmostEqual(result['city_area_m2'],256,delta=1e-4)
        self.assertAlmostEqual(result['city_archive_available_fraction'],.5,places=6)
        self.assertAlmostEqual(result['city_indexed_but_missing_archive_area_m2'],128,delta=1e-4)
        with self.assertRaises(ValueError):municipal_coverage(dict(plan,boundary_geometry_sha256='changed'),document,survey)

    @unittest.skipUnless(METADATA.is_file(),'Local frozen publisher metadata required')
    def test_actual_publisher_index_retains_tile_identity_and_units(self):
        rows,provenance=survey_index(METADATA)
        self.assertEqual(len(rows),5337)
        tile=next(t for t in rows if t['id']=='17509050')
        for actual,expected in zip(tile['native_geometry'].bounds,[1175000,1905000,1177500,1907500]):
            self.assertAlmostEqual(actual,expected,places=2)
        self.assertEqual(provenance['native_crs'],'EPSG:6455')


if __name__=='__main__':unittest.main()
