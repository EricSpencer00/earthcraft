import sys
from pathlib import Path
import unittest
import numpy as np
from shapely.geometry import box

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from roofer_regular_probe import select


class RegularProbeTests(unittest.TestCase):
    def test_selection_is_lowest_eligible_objectid(self):
        class Obj(dict):
            __getattr__=dict.__getitem__
        objects=[Obj(source_id=833197,height_m=10,geometry=box(0,0,10,10)),Obj(source_id=20,height_m=10,geometry=box(12,0,22,10)),Obj(source_id=10,height_m=10,geometry=box(24,0,34,10))]
        import roofer_regular_probe
        original=roofer_regular_probe.county_objects;roofer_regular_probe.county_objects=lambda *_:objects
        try:
            xyz=np.repeat(np.array([[13,1,2],[25,1,2]]),100,axis=0)
            obj,_,_=select(Path('.'),{'west':0,'north':40,'size':40},xyz,np.zeros(len(xyz),bool))
        finally:roofer_regular_probe.county_objects=original
        self.assertEqual(obj.source_id,10)


if __name__=='__main__':unittest.main()
