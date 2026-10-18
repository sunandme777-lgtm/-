#!/usr/bin/env python3
"""Download a release snapshot, verify every published SHA256, and emit a receipt."""
import concurrent.futures, datetime, hashlib, json, os, pathlib, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
API = 'https://api.github.com/repos/Johnbrick123/sp500-data/releases/tags/data'

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'JohnbrickDriveSync/1.0'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def download_asset(a):
    name = a['name']
    if pathlib.Path(name).name != name:
        raise ValueError('Unsafe filename')
    path = ROOT / 'assets' / name
    expected = a.get('digest') or ''
    if path.exists() and path.stat().st_size == a['size']:
        digest = 'sha256:' + hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
        if not expected or digest == expected:
            return {**{k:a.get(k) for k in ('id','name','size','updated_at','browser_download_url')},'sha256':digest[7:],'status':'UNCHANGED'}
    part = path.with_name(path.name + '.part')
    for attempt in range(3):
        try:
            req=urllib.request.Request(a['browser_download_url'],headers={'User-Agent':'JohnbrickDriveSync/1.0'})
            h=hashlib.sha256(); size=0
            with urllib.request.urlopen(req,timeout=90) as r,part.open('wb') as f:
                while True:
                    b=r.read(1024*1024)
                    if not b: break
                    f.write(b);h.update(b);size+=len(b)
            digest='sha256:'+h.hexdigest()
            if size != a['size']: raise ValueError(f'Size mismatch: {name}')
            if expected and digest != expected: raise ValueError(f'Digest mismatch: {name}')
            os.replace(part,path)
            return {**{k:a.get(k) for k in ('id','name','size','updated_at','browser_download_url')},'sha256':h.hexdigest(),'status':'DOWNLOADED'}
        except Exception:
            if attempt == 2: raise
            time.sleep(2*(attempt+1))

def main():
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    release=fetch_json(API)
    (ROOT/'assets').mkdir(parents=True,exist_ok=True)
    (ROOT/'release.json').write_text(json.dumps(release,ensure_ascii=False),encoding='utf-8')
    rows=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(download_asset,a):a for a in release['assets']}
        for f in concurrent.futures.as_completed(futures):
            rows.append(f.result())
            if len(rows)%25==0: print(f'downloaded/verified {len(rows)}/{len(futures)}',flush=True)
    rows.sort(key=lambda a:a['name'])
    receipt={'repository':'Johnbrick123/sp500-data','release_id':release['id'],'release_url':release['html_url'],'started_at':started,'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'asset_count':len(rows),'total_bytes':sum(a['size'] for a in rows),'assets':rows}
    (ROOT/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in receipt.items() if k!='assets'}),flush=True)

if __name__=='__main__': main()
