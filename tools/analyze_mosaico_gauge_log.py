"""Analyze internal counter consistency, NOT physical battery calibration.

Reads the CRC-verified system-log export's records.jsonl. Never bridges boots,
invalid observations, nonnegative current, long gaps, or implausible counter jumps.
"""
import argparse,json
from pathlib import Path

REQUIRED=(1<<0)|(1<<1)|(1<<2)|(1<<4)|(1<<5)|(1<<7)
def delta16(a,b):return ((b-a+32768)&65535)-32768

def analyze(rows):
    segments=[];segment=[];previous=None;startup=[]
    def finish():
        if len(segment)<2:return
        first,last=segment[0],segment[-1];a,b=first['gauge'],last['gauge']
        raw=sum(delta16(x['gauge']['raw_cc'],y['gauge']['raw_cc']) for x,y in zip(segment,segment[1:]))
        duration=(last['monotonic_ms']-first['monotonic_ms'])/1000
        rm=b['remaining_mah']-a['remaining_mah']
        segments.append(dict(boot=first['page_boot'],samples=len(segment),duration_seconds=duration,
            soc_start=a['soc'],soc_end=b['soc'],rm_delta_mah=rm,raw_cc_delta_mah=raw,
            rm_plus_raw_change_mah=rm+raw,voltage_start_mv=a['voltage_mv'],voltage_end_mv=b['voltage_mv'],
            plateau_observed=(duration>=600 and raw>=3 and rm==0),
            mode_values=sorted(set(x['gauge']['mode'] for x in segment if x['gauge']['valid_mask']&(1<<12))),
            physical_calibration_proven=False))
    for row in rows:
        if row.get('type')=='gauge_boot':
            startup.append(dict(boot=row['page_boot'],monotonic_ms=row['monotonic_ms'],gauge=row.get('gauge',{})))
        if row.get('type')!='gauge':continue
        g=row.get('gauge',{});valid=(g.get('valid_mask',0)&REQUIRED)==REQUIRED
        if not valid or g['current_ma']>=0 or not 1<=g['fcc_mah']<=100:
            finish();segment=[];previous=None;continue
        if previous:
            gap=row['monotonic_ms']-previous['monotonic_ms']
            d=delta16(previous['gauge']['raw_cc'],g['raw_cc'])
            if row['page_boot']!=previous['page_boot'] or not 0<gap<=180000 or abs(d)>100 or g['fcc_mah']!=previous['gauge']['fcc_mah']:
                finish();segment=[]
        segment.append(row);previous=row
    finish()
    return dict(schema=1,startup_observations=startup,segments=segments,claim='internal counter consistency only; no independent current/capacity measurement')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('records',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    rows=[json.loads(x) for x in a.records.read_text(encoding='utf8').splitlines() if x.strip()]
    with a.output.open('x',encoding='utf8') as f:json.dump(analyze(rows),f,indent=2)

if __name__=='__main__':main()
