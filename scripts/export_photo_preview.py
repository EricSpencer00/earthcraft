"""Export observed stereo surface voxels through Minecraft's native function loader."""
import hashlib,json,math,zipfile,io,collections
from pathlib import Path
import numpy as np
from PIL import Image
import nbtlib as n
OUT=Path(__file__).resolve().parents[1]/'runs/imagery-proof'
source=OUT/'dense-multiview.npz';data=np.load(source);xyz=data['xyz'];rgb=data['rgb']
# Rigid axis permutation only. Dataset z-up is an explicit preview assumption.
coords=xyz[:,[1,2,0]]*np.array([1,1,-1]);origin=np.floor(coords.min(axis=0));blocks=np.floor(coords-origin).astype(int)+[0,80,0]
assert np.max(np.ptp(blocks,axis=0))<32,'Unexpected extent: refuse unbounded export'
unique,inverse,counts=np.unique(blocks,axis=0,return_inverse=True,return_counts=True)
colors=np.stack([np.bincount(inverse,weights=rgb[:,i],minlength=len(unique))/counts for i in range(3)],axis=1)
# Palette appearance measured from installed game textures, not hand-tuned landmark colors.
base=Path.home()/'Library/Application Support/minecraft';jar=base/'versions/1.21.10/1.21.10.jar'
palette={}
with zipfile.ZipFile(jar) as z:
 for name in z.namelist():
  if not name.startswith('assets/minecraft/textures/block/') or not name.endswith('.png'):continue
  block=Path(name).stem
  if not (block.endswith('_concrete') or (block.endswith('_terracotta') and not block.endswith('_glazed_terracotta')) or block in ['bricks','stone','stone_bricks','sandstone','smooth_stone','terracotta','deepslate']):continue
  image=np.asarray(Image.open(io.BytesIO(z.read(name))).convert('RGB'))
  palette[block]=image.reshape(-1,3).mean(axis=0)
keys=sorted(palette);pc=np.array([palette[k] for k in keys]);chosen=np.argmin(((colors[:,None]-pc[None])**2).sum(axis=2),axis=1)
world=base/'saves/Earthcraft-Photo-Surface-v2'
if world.exists():raise FileExistsError(world)
world.mkdir();level=n.load(base/'saves/Earthcraft-Water-Tower-64m/level.dat');d=level['Data']
d['LevelName']=n.String('Earthcraft PHOTO surface v2');d['GameType']=n.Int(3);d['allowCommands']=n.Byte(1);d['DayTime']=n.Long(6000)
d.pop('Player',None);d['ScheduledEvents']=n.List[n.Compound]([])
gen=d['WorldGenSettings'];gen['generate_features']=n.Byte(0)
settings=gen['dimensions']['minecraft:overworld']['generator']['settings'];settings['layers']=n.List[n.Compound]([n.Compound({'block':n.String('minecraft:air'),'height':n.Int(1)})]);settings['structure_overrides']=n.List[n.String]([])
d['DataPacks']=n.Compound({'Enabled':n.List[n.String]([n.String('vanilla'),n.String('file/photo')]),'Disabled':n.List[n.String]([])})
# Import calibration parser only to position the preview camera, never for source geometry.
import runpy
s=runpy.run_path(str(Path(__file__).with_name('photo_pair_probe.py')));_,R,t,_=s['poses'][0];camera=-R.T@t;camera=camera[[1,2,0]]*[1,1,-1]-origin+[0,80,0]
forward=(R.T@np.array([0,0,1]))[[1,2,0]]*[1,1,-1];yaw=math.degrees(math.atan2(-forward[0],forward[2]));pitch=-math.degrees(math.asin(forward[1]))
if 'spawn' in d:d['spawn']=n.Compound({'pos':n.IntArray(np.floor(camera).astype(np.int32)),'angle':n.Float(yaw),'dimension':n.String('minecraft:overworld')})
level.save(world/'level.dat')
pack=world/'datapacks/photo';(pack/'data/earthcraft/function').mkdir(parents=True);(pack/'data/minecraft/tags/function').mkdir(parents=True)
(pack/'pack.mcmeta').write_text(json.dumps({'pack':{'pack_format':88,'min_format':[88,0],'max_format':[88,0],'description':'Photo-derived surface; no procedural building geometry'}}))
(pack/'data/minecraft/tags/function/tick.json').write_text(json.dumps({'values':['earthcraft:tick']}))
(pack/'data/earthcraft/function/tick.mcfunction').write_text('execute as @a[tag=!earthcraft_photo_loaded] run function earthcraft:build\n')
commands=['gamemode spectator @s','gamerule doDaylightCycle false','weather clear','time set noon']
commands += [f'setblock {x} {y} {z} minecraft:{keys[i]}' for (x,y,z),i in zip(unique,chosen)]
commands += [f'tp @s {camera[0]:.3f} {camera[1]:.3f} {camera[2]:.3f} {yaw:.3f} {pitch:.3f}','tag @s add earthcraft_photo_loaded']
(pack/'data/earthcraft/function/build.mcfunction').write_text('\n'.join(commands)+'\n')
report={'status':'exported_unverified_preview','world':str(world),'source_sha256':hashlib.file_digest(source.open('rb'),'sha256').hexdigest(),'source_points':len(xyz),'surface_blocks':len(unique),'extent_blocks':np.ptp(unique,axis=0).tolist(),'blocks':[{'xyz':p.tolist(),'block':keys[i],'source_samples':int(c)} for p,i,c in zip(unique,chosen,counts)],'scale':'one block per dataset unit; metric provenance pending','orientation':'rigid axis permutation; dataset Z-up assumed, not independently verified','unknown_surfaces':'air; no solid filling or interior generation','geometry_source':'local calibrated stereo only','appearance_source':'mean sampled photo RGB mapped to installed block texture means; not a validated material classifier','local_vision_observation_used_for_geometry':False,'game_load_verified':False,'camera':camera.tolist(),'yaw':yaw,'pitch':pitch}
(OUT/'voxel-preview-v2.json').write_text(json.dumps(report,indent=2));print(world,'surface blocks',len(unique))
