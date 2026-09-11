import sys
import unittest
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from naip_imagery import choose_source, spectral_masks, source_crs,normalize_crop
from imagery_alignment_audit import edge_alignment


class NaipTests(unittest.TestCase):
    def test_normalization_budget_cannot_silently_expand(self):
        meta={'size':16,'west':0,'north':16,'crs':'EPSG:3857'}
        for bound in (0,4_000_001,4_000_000.0):
            with self.assertRaisesRegex(ValueError,'pixel budget'):normalize_crop('not-opened.tif',meta,bound)
    def test_exact_acquisition_not_publication_year_controls_temporal_choice(self):
        meta={'size':16,'west':0,'north':16,'crs':'EPSG:3857'}
        def record(identity,capture,year):
            return {'attributes':{'Category':1,'band_count':4,'resolution_units':'METER',
                'resolution_value':.6,'Year':year,'OBJECTID':identity,
                'acquisition_date':datetime.fromisoformat(capture).replace(tzinfo=timezone.utc).timestamp()*1000},
                'geometry':{'rings':[[[-1,-1],[1,-1],[1,1],[-1,1],[-1,-1]]]}}
        a=record(1,'2022-01-01',2022);b=record(2,'2021-12-31',2021)
        self.assertEqual(choose_source([a,b],meta,target_capture='2021-12-31')['attributes']['OBJECTID'],2)
        a['attributes'].pop('acquisition_date')
        with self.assertRaises(ValueError):choose_source([a],meta,target_capture='2022-01-01')
    def test_source_projection_follows_metadata_not_chicago(self):
        for zone,epsg in [('16N',26916),('10N',26910)]:
            self.assertEqual(source_crs({'projection_name':'UTM','projection_zone':zone,'datum':'NAD83'}),epsg)
        with self.assertRaises(ValueError):source_crs({'projection_name':'UTM','projection_zone':'16S','datum':'NAD83'})

    def test_known_image_edge_translation_and_empty_evidence(self):
        boundary=np.zeros((64,64),bool)
        boundary[20,20:40]=True;boundary[39,20:40]=True
        boundary[20:40,20]=True;boundary[20:40,39]=True
        edges=np.roll(np.roll(boundary,3,axis=1),-2,axis=0)
        result=edge_alignment(boundary,edges)
        self.assertEqual(result['best_shift_xz_m'],[3,-2])
        self.assertEqual(result['fitted_mean_edge_distance_m'],0)
        with self.assertRaises(ValueError):edge_alignment(boundary,np.zeros_like(boundary))

    def test_spectral_flags_do_not_fill_missing_or_dark_pixels(self):
        bands=np.array([[[100,10,100,0]],[[100,10,120,0]],[[100,10,80,0]],[[100,20,240,0]]],np.uint8)
        valid=np.array([[True,True,True,False]])
        ndvi,shadow,vegetation,usable=spectral_masks(bands,valid)
        np.testing.assert_array_equal(shadow,[[False,True,False,False]])
        np.testing.assert_array_equal(vegetation,[[False,True,True,False]])
        np.testing.assert_array_equal(usable,[[True,False,False,False]])
        self.assertTrue(np.isnan(ndvi[0,3]))

    def test_no_coverage_does_not_become_a_mosaic_or_default(self):
        meta={'size':16,'west':0,'north':16,'crs':'EPSG:3857'}
        with self.assertRaises(ValueError):choose_source([],meta)
        feature={'attributes':{'Category':1,'band_count':4,'resolution_units':'METER',
                   'resolution_value':.6,'Year':2019,'OBJECTID':1},
                 'geometry':{'rings':[[[10,10],[11,10],[11,11],[10,11],[10,10]]]}}
        with self.assertRaises(ValueError):choose_source([feature],meta)

    def test_single_covering_source_and_closest_year_are_selected(self):
        meta={'size':16,'west':0,'north':16,'crs':'EPSG:3857'}
        def feature(year,identity):
            return {'attributes':{'Category':1,'band_count':4,'resolution_units':'METER',
                    'resolution_value':.6,'Year':year,'OBJECTID':identity},
                    'geometry':{'rings':[[[-1,-1],[1,-1],[1,1],[-1,1],[-1,-1]]]}}
        selected=choose_source([feature(2019,1),feature(2023,2)],meta,2022)
        self.assertEqual(selected['attributes']['OBJECTID'],2)


if __name__=='__main__':unittest.main()
