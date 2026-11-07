"""Cache a small real CARTO/OSM demo area. No synthetic map images are generated."""
import math, pathlib, urllib.request, concurrent.futures, json
root=pathlib.Path(__file__).resolve().parents[1]/'public/maps/tiles'
def tile(lon,lat,z):
 n=2**z
 return int((lon+180)/360*n),int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)
jobs=[]
for z in range(12,17):
 x0,y0=tile(120.175,30.285,z);x1,y1=tile(120.27,30.2,z)
 for x in range(x0-1,x1+2):
  for y in range(y0-1,y1+2):jobs.append((z,x,y))
def fetch(t):
 z,x,y=t;p=root/f'{z}/{x}/{y}.png'
 if p.exists():return True
 p.parent.mkdir(parents=True,exist_ok=True)
 try:
  u=f'https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png'
  req=urllib.request.Request(u,headers={'User-Agent':'YunchouICTDemo/1.0'})
  with urllib.request.urlopen(req,timeout=30) as r:data=r.read()
  if not data.startswith(b'\x89PNG'):return False
  p.write_bytes(data);return True
 except Exception:return False
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(fetch,jobs))
print(json.dumps({'tiles':len(jobs),'cached':sum(results),'failed':len(results)-sum(results)}))
