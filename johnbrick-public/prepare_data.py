#!/usr/bin/env python3
"""Prepare complete release storage and bounded, typed Sheets data (no credentials)."""
import csv, datetime as dt, hashlib, json, pathlib, re, subprocess, zipfile

ROOT=pathlib.Path(__file__).resolve().parent
ASSETS=ROOT/'assets'
OUT=ROOT/'prepared'
CORE={'prices.parquet','raw.tar.zst','members.parquet','membership_intervals.parquet'}
PART_BYTES=80*1024*1024

def query(sql):
    result=subprocess.run([str(ROOT/'bin'/'duckdb'),'-json','-c',sql],capture_output=True,text=True,check=True)
    return json.loads(result.stdout or '[]')

def src(name):
    return "read_parquet('"+str(ASSETS/name).replace("'","''")+"')"

def table(name,records,dates=(),numbers=(),note=''):
    headers=list(records[0]) if records else []
    return dict(name=name,headers=headers,rows=[[r.get(k) for k in headers] for r in records],dates=list(dates),numbers=list(numbers),note=note)

def csv_table(name,filename,dates=(),numbers=()):
    with (ASSETS/filename).open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);headers=reader.fieldnames or [];rows=list(reader)
    for r in rows:
        for k in numbers:
            if r.get(k) not in ('',None):
                try:r[k]=float(r[k])
                except ValueError:pass
    result=table(name,rows,dates,numbers)
    if not result['headers']:result['headers']=headers
    return result

def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def main():
    OUT.mkdir(exist_ok=True)
    p=src('prices.parquet');m=src('members.parquet');i=src('membership_intervals.parquet')
    stats=query(f"SELECT count(*) AS price_rows,count(DISTINCT ticker) AS tickers,CAST(min(date) AS DATE)::VARCHAR AS first_date,CAST(max(date) AS DATE)::VARCHAR AS last_date,count(*) FILTER(WHERE date IS NULL OR ticker IS NULL) AS null_keys FROM {p}")[0]
    stats.update(query(f"SELECT count(*) AS member_rows,count(DISTINCT ticker) AS historical_members,CAST(max(date) AS DATE)::VARCHAR AS membership_date FROM {m}")[0])
    stats['duplicate_keys']=query(f"SELECT count(*) AS n FROM (SELECT date,ticker FROM {p} GROUP BY ALL HAVING count(*)>1)")[0]['n']
    stats['current_members']=query(f"SELECT count(DISTINCT ticker) AS n FROM {m} WHERE date=(SELECT max(date) FROM {m})")[0]['n']
    if stats['null_keys'] or stats['duplicate_keys'] or stats['price_rows']<=0 or stats['member_rows']<=0: raise ValueError('Price or membership validation failed; retain previous successful snapshot')
    latest=query(f"""WITH s AS (SELECT ticker,CAST(min(date) AS DATE)::VARCHAR AS first_date,count(*) AS price_rows FROM {p} GROUP BY ticker),
      l AS (SELECT * FROM {p} QUALIFY row_number() OVER(PARTITION BY ticker ORDER BY date DESC)=1),
      c AS (SELECT DISTINCT ticker FROM {m} WHERE date=(SELECT max(date) FROM {m}))
      SELECT l.ticker,CAST(l.date AS DATE)::VARCHAR AS date,l.open,l.high,l.low,l.close,l.adj_close,l.volume,l.dividends,l.stock_splits,l.source,
      (c.ticker IS NOT NULL) AS is_current_member,s.first_date,s.price_rows FROM l JOIN s USING(ticker) LEFT JOIN c USING(ticker) ORDER BY l.ticker""")
    intervals=query(f'SELECT ticker,CAST(start AS DATE)::VARCHAR AS start,CAST("end" AS DATE)::VARCHAR AS "end" FROM {i} ORDER BY ticker,start')
    annual=query(f"SELECT year(date) AS year,source,count(*) AS price_rows,count(DISTINCT ticker) AS tickers,count(open) AS open_present,count(volume) AS volume_present FROM {p} GROUP BY ALL ORDER BY year,source")
    tabs=[table('01_최신가격',latest,('date','first_date'),('open','high','low','close','adj_close','volume','dividends','stock_splits','price_rows')),
          table('02_편입편출이력',intervals,('start','end')),
          table('03_연도별출처',annual,(),('year','price_rows','tickers','open_present','volume_present')),
          csv_table('04_거래공백','no_trade_days.csv',('date',)),
          csv_table('05_퇴출공백','exit_gaps.csv',('removed','last_member_day','last_trade','next_trade'),('last_close','next_close','gap_days','change')),
          csv_table('06_종목명변경','renames.csv'),
          csv_table('07_구성변경원문','wiki_changes.csv',('date',))]
    verification=(ASSETS/'verification_report.txt').read_text(encoding='utf-8')
    match=re.search(r'PASS\s+(\d+)\s+FAIL\s+(\d+)\s+UNVERIFIED\s+(\d+)',verification)
    stats['verification_summary']=f'PASS {match[1]} / FAIL {match[2]} / UNVERIFIED {match[3]}' if match else '원문 확인 필요'
    report=[]
    for name in ['verification_report.txt','nightly_guard.txt','nightly_alert.txt']:
        for n,line in enumerate((ASSETS/name).read_text(encoding='utf-8').splitlines(),1):
            report.append({'파일':name,'행':n,'내용':line})
    tabs.append(table('09_검증보고서',report,(),('행',)))
    receipt=json.loads((ROOT/'receipt.json').read_text())
    fingerprint=hashlib.sha256(json.dumps([(a['name'],a['size'],a['sha256']) for a in receipt['assets']],separators=(',',':')).encode()).hexdigest()
    storage=[]
    for name in sorted(CORE):
        source=ASSETS/name
        if source.stat().st_size<=PART_BYTES:
            storage.append({'name':name,'path':str(source),'size':source.stat().st_size,'sha256':sha(source),'source':name,'part':1,'parts':1})
        else:
            parts=(source.stat().st_size+PART_BYTES-1)//PART_BYTES
            with source.open('rb') as f:
                for j in range(parts):
                    path=OUT/f'{name}.part{j+1:03d}'
                    path.write_bytes(f.read(PART_BYTES))
                    storage.append({'name':path.name,'path':str(path),'size':path.stat().st_size,'sha256':sha(path),'source':name,'part':j+1,'parts':parts})
    archive=OUT/'supporting_data.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for a in receipt['assets']:
            if a['name'] not in CORE:z.write(ASSETS/a['name'],a['name'])
        z.write(ROOT/'release.json','release.json')
        z.write(ROOT/'receipt.json','receipt.json')
    storage.append({'name':archive.name,'path':str(archive),'size':archive.stat().st_size,'sha256':sha(archive),'source':f"{len(receipt['assets'])-len(CORE)} supporting release assets + metadata",'part':1,'parts':1})
    state=dict(fingerprint=fingerprint,stats=stats,source_receipt=receipt,storage=storage,created_at=dt.datetime.now(dt.timezone.utc).isoformat())
    (OUT/'snapshot.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'tables.json').write_text(json.dumps(tabs,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print(json.dumps({'fingerprint':fingerprint,'stats':stats,'tabs':[{ 'name':t['name'],'rows':len(t['rows']),'columns':len(t['headers'])} for t in tabs],'files':[{k:v for k,v in f.items() if k!='path'} for f in storage]},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
