"""Bounded visual surface experiment: photo-derived 1/16-unit cubes, no collision."""
import io,json,hashlib,time,resource,zipfile,shutil,runpy,math,argparse
from pathlib import Path
import numpy as np
from PIL import Image
import nbtlib as n
from local_paths import imagery_root
parser=argparse.ArgumentParser();parser.add_argument('--courtyard',action='store_true');args=parser.parse_args()
start=time.monotonic();root=Path(__file__).resolve().parents[1];out=root/'runs/imagery-proof'
version=5 if args.courtyard else 6
world=root/f'worlds/Earthcraft-Photo-Detail-v{version}'
if world.exists():raise FileExistsError(world)
source=imagery_root()/'courtyard-run/courtyard-inferred.npz' if args.courtyard else out/'dense-multiview.npz'
data=np.load(source);xyz=data['xyz'];rgb=data['rgb'];crop=None
if args.courtyard:
 baseline=np.load(out/'dense-multiview.npz')['xyz'];lo=baseline.min(axis=0)-1;hi=baseline.max(axis=0)+1
 mask=((xyz>=lo)&(xyz<=hi)).all(axis=1);xyz=xyz[mask];rgb=rgb[mask];crop={'min':lo.tolist(),'max':hi.tolist(),'selection':'original training reconstruction bounds plus one dataset unit; no scan or held-out image selection'}
coords=xyz[:,[1,2,0]]*[1,1,1];origin=np.floor(coords.min(axis=0));coords-=origin
micro,inverse,counts=np.unique(np.floor(coords*16).astype(int),axis=0,return_inverse=True,return_counts=True)
assert len(micro)<100000
colors=np.stack([np.bincount(inverse,weights=rgb[:,i])/counts for i in range(3)],axis=1).round().astype('uint8')
chunks,group=np.unique(micro//16,axis=0,return_inverse=True)
world.mkdir(parents=True)
base=Path.home()/'Library/Application Support/minecraft'
level=n.load(base/'saves/Earthcraft-Photo-Surface-v2/level.dat');level['Data']['LevelName']=n.String(f'Earthcraft PHOTO detail v{version}');level['Data'].pop('Player',None);level.save(world/'level.dat')
pack=world/'datapacks/photo';(pack/'data/earthcraft/function').mkdir(parents=True);(pack/'data/minecraft/tags/function').mkdir(parents=True)
(pack/'pack.mcmeta').write_text(json.dumps({'pack':{'pack_format':88,'min_format':[88,0],'max_format':[88,0],'description':'Photo surface visual test'}}))
(pack/'data/minecraft/tags/function/tick.json').write_text('{"values":["earthcraft:tick"]}')
(pack/'data/earthcraft/function/tick.mcfunction').write_text('execute as @a[tag=!earthcraft_detail_loaded] run function earthcraft:build\n')
commands=['gamemode spectator @s','time set noon','weather clear','gamerule doDaylightCycle false']
faces=('north','south','east','west','up','down')
# Each cell gets its own constant pixel in a small atlas; no generated pattern.
with zipfile.ZipFile(world/'resources.zip','w',zipfile.ZIP_DEFLATED) as z:
 z.writestr('pack.mcmeta',json.dumps({'pack':{'pack_format':69,'min_format':[69,0],'max_format':[69,0],'description':'ETH3D photo-derived surface, CC BY-NC-SA 4.0; visual only'}}))
 for i,chunk in enumerate(chunks):
  ids=np.flatnonzero(group==i);side=2**math.ceil(math.log2(math.ceil(math.sqrt(len(ids)))))
  atlas=np.zeros((side,side,3),dtype='uint8');atlas.reshape(-1,3)[:len(ids)]=colors[ids]
  b=io.BytesIO();Image.fromarray(atlas).save(b,format='PNG');z.writestr(f'assets/earthcraft/textures/block/surface_{i}.png',b.getvalue())
  elements=[]
  for j,k in enumerate(ids):
   p=(micro[k]%16).tolist();u=(j%side+.5)*16/side;v=(j//side+.5)*16/side
   elements.append({'from':p,'to':[a+1 for a in p],'shade':False,'faces':{f:{'texture':'#surface','uv':[u,v,u,v]} for f in faces}})
  z.writestr(f'assets/earthcraft/models/surface_{i}.json',json.dumps({'ambientocclusion':False,'textures':{'surface':f'earthcraft:block/surface_{i}','particle':f'earthcraft:block/surface_{i}'},'elements':elements},separators=(',',':')))
  z.writestr(f'assets/earthcraft/items/surface_{i}.json',json.dumps({'model':{'type':'minecraft:model','model':f'earthcraft:surface_{i}'}}))
  x,y,zp=chunk+[.5,80.5,.5]
  commands.append(f'summon minecraft:item_display {x} {y} {zp} {{Tags:["earthcraft_surface"],item:{{id:"minecraft:stone",count:1,components:{{"minecraft:item_model":"earthcraft:surface_{i}"}}}},item_display:"none",Rotation:[180f,0f],brightness:{{block:15,sky:15}},view_range:4f,width:2f,height:2f}}')
s=runpy.run_path(str(root/'scripts/photo_pair_probe.py'));cameras=[]
for index in (0,2):
 name,R,t,K=s['poses'][index];camera=(-R.T@t)[[1,2,0]]*[1,1,1]-origin+[0,80,0]
 forward=(R.T@np.array([0,0,1]))[[1,2,0]]*[1,1,1]
 yaw=math.degrees(math.atan2(-forward[0],forward[2]));pitch=-math.degrees(math.asin(forward[1]))
 # tp is feet position; spectator eye sits 1.62 units above it.
 command=f'tp @s {camera[0]:.5f} {camera[1]-1.62:.5f} {camera[2]:.5f} {yaw:.5f} {pitch:.5f}'
 (pack/f'data/earthcraft/function/view_{index}.mcfunction').write_text(command+'\n');cameras.append({'source':name,'eye':camera.tolist(),'command':command})
commands += [cameras[0]['command'],'tag @s add earthcraft_detail_loaded']
(pack/'data/earthcraft/function/build.mcfunction').write_text('\n'.join(commands)+'\n')
report={'status':'exported_not_loaded','world':str(world),'source_sha256':hashlib.file_digest(source.open('rb'),'sha256').hexdigest(),'world_axis_matrix':[[0,1,0],[0,0,1],[1,0,0]],'crop':crop,'surface_cells':len(micro),'display_entities':len(chunks),'cell_size_dataset_units':1/16,'world_scale':'one block per dataset unit, metric/gravity provenance pending','cameras':cameras,'item_display_rotation_y_degrees':180,'collision':False,'source':'local stereo geometry and photo colours; no reference scan','appearance':'mean observed colour per 1/16 cell; fullbright removes additional Minecraft shading; photographed shadows retained','unknown':'missing surface is empty, no filling','elapsed_seconds':time.monotonic()-start,'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
(out/f'detail-export-v{version}.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
# Install a new copy, preserving project output and every existing save.
dest=base/'saves'/world.name
if dest.exists():raise FileExistsError(dest)
shutil.copytree(world,dest)
