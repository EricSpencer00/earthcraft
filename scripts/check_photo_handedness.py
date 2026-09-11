"""Validate scene handedness and camera-right direction against supplied calibration."""
import json,runpy
from pathlib import Path
import numpy as np
root=Path(__file__).resolve().parents[1];out=root/'runs/imagery-proof'
s=runpy.run_path(str(root/'scripts/photo_pair_probe.py'))
record=json.loads((out/'detail-export-v5.json').read_text());A=np.array(record['world_axis_matrix'])
assert np.allclose(A@A.T,np.eye(3)) and np.isclose(np.linalg.det(A),1)
views=[]
for index,camera in zip((0,2),record['cameras']):
 R=s['poses'][index][1];right=A@R.T@np.array([1,0,0]);forward=A@R.T@np.array([0,0,1])
 yaw=np.deg2rad(float(camera['command'].split()[-2]));pitch=np.deg2rad(float(camera['command'].split()[-1]))
 game_forward=np.array([-np.sin(yaw)*np.cos(pitch),-np.sin(pitch),np.cos(yaw)*np.cos(pitch)])
 game_right=np.array([-np.cos(yaw),0,-np.sin(yaw)])
 alignment=float(right@game_right)
 assert alignment>.99 and np.allclose(forward,game_forward,atol=1e-6)
 views.append({'index':index,'camera_right_dot_game_right':alignment,'forward_matches':True,'roll_not_matched':True})
report={'status':'passed','matrix_determinant':float(np.linalg.det(A)),'views':views,'scope':'proper rigid axis rotation and non-mirrored camera; metric scale and gravity are separate'}
(out/'handedness-audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
