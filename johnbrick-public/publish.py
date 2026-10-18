#!/usr/bin/env python3
"""Publish only public Johnbrick-derived data. No Google credentials or IDs."""
import argparse, datetime as dt, gzip, hashlib, json, os, pathlib, shutil
import urllib.error, urllib.parse, urllib.request, zipfile

ROOT=pathlib.Path(__file__).resolve().parent
UPSTREAM='https://api.github.com/repos/Johnbrick123/sp500-data/releases/tags/data'
API='https://api.github.com'
TAG='johnbrick-current'

def sha(path,algorithm='sha256'):
    with path.open('rb') as f:return hashlib.file_digest(f,algorithm).hexdigest()

def api(path,method='GET',body=None,raw=False,content_type=None):
    url=path if path.startswith('https://') else API+path
    host=urllib.parse.urlparse(url).hostname
    if host not in ('api.github.com','uploads.github.com'):raise ValueError('Invalid GitHub API host')
    headers={'User-Agent':'JohnbrickDirectSync/2.0','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:headers['Authorization']='Bearer '+token
    if body is not None:
        if not raw:body=json.dumps(body).encode()
        headers['Content-Type']=content_type or ('application/octet-stream' if raw else 'application/json')
    request=urllib.request.Request(url,data=body,headers=headers,method=method)
    with urllib.request.urlopen(request,timeout=180) as r:
        data=r.read()
        return json.loads(data) if data else {}

def fingerprint(release):
    rows=sorted((a['name'],a['size'],(a.get('digest') or '').removeprefix('sha256:')) for a in release['assets'])
    if any(len(r[2])!=64 for r in rows):raise ValueError('Every source asset must have a published SHA256')
    return hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()

def ensure_duckdb():
    exe=ROOT/'bin'/'duckdb'
    if exe.exists():exe.chmod(0o755);return
    release=api('/repos/duckdb/duckdb/releases/tags/v1.5.6')
    asset=next(a for a in release['assets'] if a['name']=='duckdb_cli-linux-amd64.zip')
    q=urllib.request.Request(asset['browser_download_url'],headers={'User-Agent':'JohnbrickDirectSync/2.0'})
    with urllib.request.urlopen(q,timeout=120) as f:data=f.read()
    expected=(asset.get('digest') or '').removeprefix('sha256:')
    if not expected or hashlib.sha256(data).hexdigest()!=expected:raise ValueError('DuckDB vendor checksum mismatch')
    import io
    exe.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:exe.write_bytes(z.read('duckdb'))
    exe.chmod(0o755)

def upload(release,path,repo):
    existing=next((a for a in release.get('assets',[]) if a['name']==path.name),None)
    if existing:
        if existing.get('digest')=='sha256:'+sha(path):return existing
        raise ValueError('Immutable output name already exists with a different digest: '+path.name)
    url=release['upload_url'].split('{')[0]+'?name='+urllib.parse.quote(path.name)
    return api(url,'POST',path.read_bytes(),True,'application/octet-stream')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--repo',default=os.environ.get('GITHUB_REPOSITORY','sunandme777-lgtm/-'));parser.add_argument('--local',action='store_true');args=parser.parse_args()
    upstream=api(UPSTREAM);fp=fingerprint(upstream)
    release=None
    if not args.local:
        try:
            release=api(f'/repos/{args.repo}/releases/tags/{TAG}')
            try:previous=json.loads(release.get('body') or '{}')
            except ValueError:previous={}
            if previous.get('fingerprint')==fp:
                print(json.dumps({'changed':False,'fingerprint':fp}));return
        except urllib.error.HTTPError as e:
            if e.code!=404:raise
    ensure_duckdb()
    import sync_source,prepare_data
    sync_source.main();prepare_data.main()
    state=json.loads((ROOT/'prepared'/'snapshot.json').read_text())
    if state['fingerprint']!=fp or fingerprint(api(UPSTREAM))!=fp:raise ValueError('Source changed during build; retry the next run')
    source={a['name']:a for a in state['source_receipt']['assets']}
    storage=[]
    for f in state['storage']:
        path=pathlib.Path(f['path']);row={k:v for k,v in f.items() if k!='path'}
        row['md5']=sha(path,'md5')
        if f['source'] in source:
            a=source[f['source']]
            row.update(download_url=a['browser_download_url'],source_start=(f['part']-1)*prepare_data.PART_BYTES,source_size=a['size'])
        storage.append(row)
    short=fp[:24];out=ROOT/'prepared'
    archive=out/f'supporting-{short}.zip'
    # Use a stable ZIP member timestamp so retries of the same source have the same bytes.
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for a in state['source_receipt']['assets']:
            if a['name'] not in prepare_data.CORE:
                info=zipfile.ZipInfo(a['name'],(2020,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                z.writestr(info,(ROOT/'assets'/a['name']).read_bytes())
        # Source metadata is stable; mutable download timestamps belong to the private receipt.
        metadata={'repository':state['source_receipt']['repository'],'release_id':state['source_receipt']['release_id'],'fingerprint':fp,'assets':[{k:a[k] for k in ('id','name','size','updated_at','browser_download_url','sha256')} for a in state['source_receipt']['assets']]}
        info=zipfile.ZipInfo('release_manifest.json',(2020,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
        z.writestr(info,json.dumps(metadata,ensure_ascii=False,separators=(',',':')).encode())
    supporting=next(f for f in storage if f['name']=='supporting_data.zip')
    supporting.update(size=archive.stat().st_size,sha256=sha(archive),md5=sha(archive,'md5'),source_start=0,source_size=archive.stat().st_size)
    tables=out/f'tables-{short}.json.gz'
    tables.write_bytes(gzip.compress((out/'tables.json').read_bytes(),mtime=0))
    receipt={k:state['source_receipt'][k] for k in ('repository','release_id','release_url','asset_count','total_bytes')}
    receipt['assets']=[{k:a[k] for k in ('id','name','size','updated_at','browser_download_url','sha256')} for a in state['source_receipt']['assets']]
    manifest={'schema_version':2,'fingerprint':fp,'stats':state['stats'],'source_receipt':receipt,'storage':storage,'tables':{'size':tables.stat().st_size,'sha256':sha(tables)},'upstream_api':UPSTREAM,'publisher_repository':args.repo,'source_snapshot_utc':max(a['updated_at'] for a in receipt['assets'])}
    # URLs are filled with the observed upload responses in production.
    prefix=f'https://github.com/{args.repo}/releases/download/{TAG}/'
    supporting['download_url']=prefix+archive.name
    manifest['tables']['download_url']=prefix+tables.name
    manifest_path=out/f'manifest-{short}.json'
    def save_manifest():manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    save_manifest()
    if args.local:
        print(json.dumps({'changed':True,'fingerprint':fp,'manifest':str(manifest_path),'tables':str(tables),'supporting':str(archive),'stats':manifest['stats']}));return
    if release is None:release=api(f'/repos/{args.repo}/releases','POST',{'tag_name':TAG,'name':'Johnbrick public data snapshots','body':'{}','draft':False,'prerelease':False})
    archive_asset=upload(release,archive,args.repo);supporting['download_url']=archive_asset['browser_download_url']
    tables_asset=upload(release,tables,args.repo);manifest['tables']['download_url']=tables_asset['browser_download_url']
    save_manifest();manifest_asset=upload(release,manifest_path,args.repo)
    pointer={'schema_version':2,'fingerprint':fp,'manifest_url':manifest_asset['browser_download_url'],'manifest_sha256':sha(manifest_path),'manifest_size':manifest_path.stat().st_size,'price_last_date':manifest['stats']['last_date'],'price_rows':manifest['stats']['price_rows']}
    api(f'/repos/{args.repo}/releases/{release["id"]}','PATCH',{'body':json.dumps(pointer,separators=(',',':'))})
    content_path=f'/repos/{args.repo}/contents/johnbrick-public/latest.json'
    import base64
    body={'message':'Update public Johnbrick snapshot '+manifest['stats']['last_date'],'content':base64.b64encode((json.dumps(pointer,indent=2)+'\n').encode()).decode(),'branch':'main'}
    try:body['sha']=api(content_path)['sha']
    except urllib.error.HTTPError as e:
        if e.code!=404:raise
    api(content_path,'PUT',body)
    print(json.dumps({'changed':True,'fingerprint':fp,'price_rows':manifest['stats']['price_rows'],'price_last_date':manifest['stats']['last_date'],'manifest_url':manifest_asset['browser_download_url']}))

if __name__=='__main__':main()
