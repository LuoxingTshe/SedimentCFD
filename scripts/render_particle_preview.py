"""Export a GIF of tracers advected in the actual final-frame velocity field."""
from pathlib import Path
import json
import numpy as np
import matplotlib.tri as tri
from PIL import Image, ImageDraw
root=Path(__file__).resolve().parents[1]
d=json.loads((root/'outputs/physical_preview/preview_data.json').read_text())
p=np.array(d['points']);t=np.array(d['triangles']);f=d['frames'][-1];uv=np.array(f['uv']);bed=np.array(f['bed']);c=d['config']
finder=tri.Triangulation(p[:,0],p[:,1],t).get_trifinder()
W,H=800,440;scale=46
P=lambda x,y:(32+x*scale,400-y*scale)
bg=Image.new('RGB',(W,H),(0,0,0));draw=ImageDraw.Draw(bg)
dep,ero=max(bed.max(),1e-12),max(-bed.min(),1e-12)  # deposition/erosion scaled separately
for ids,b in zip(t,bed):
 a=min(1,b/dep if b>0 else -b/ero);rgb=np.array([255,140,0] if b>0 else [40,120,255])*a  # black = no change
 draw.polygon([P(*p[j]) for j in ids], fill=tuple(rgb.astype(int)))
for g in c['gabions']:
 a=np.radians(g['angle']);cs=[]
 for sx,sy in [(-1,-1),(1,-1),(1,1),(-1,1)]:
  u,v=sx*g['length']/2,sy*g['width']/2
  cs.append(P(g['x']+u*np.cos(a)-v*np.sin(a),g['y']+u*np.sin(a)+v*np.cos(a)))
 draw.line(cs+[cs[0]],fill=(255,255,255),width=2)
draw.text((32,12),'THETIS | actual flow at t = 60 s | tracer animation 3x',fill=(220,238,240))
draw.text((32,416),'Tracers visualize water velocity, not individual sediment grains.',fill=(171,192,199))
rng=np.random.default_rng(7);xy=rng.random((850,2))*[c['length'],c['width']];trail=bg.copy();images=[]
for frame in range(100):
 old=xy.copy();idx=finder(xy[:,0],xy[:,1]);valid=idx>=0;v=uv[np.maximum(idx,0)];mid=xy+.09*v;j=finder(mid[:,0],mid[:,1]);v=np.where((j>=0)[:,None],uv[np.maximum(j,0)],v);xy+=.18*v
 bad=(~valid)|(xy[:,0]<0)|(xy[:,0]>=c['length'])|(xy[:,1]<0)|(xy[:,1]>=c['width']);xy[bad]=rng.random((bad.sum(),2))*[.1,c['width']]
 trail=Image.blend(trail,bg,.17);dr=ImageDraw.Draw(trail)
 for a,b in zip(old[~bad],xy[~bad]):dr.line([P(*a),P(*b)],fill=(180,234,245),width=1)
 images.append(trail.copy())
images[0].save(root/'outputs/physical_preview/particles.gif',save_all=True,append_images=images[1:],duration=60,loop=0,optimize=False)
print('GIF ready')
