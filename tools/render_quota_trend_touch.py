"""Geometry-faithful offline preview; synthetic fixtures, not device evidence.
Pillow text approximates LVGL Montserrat. Plot dimensions match production.
"""
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from datetime import datetime,timezone,timedelta
R=Path(__file__).resolve().parents[1]
OUT=R/'.artifacts/mosaico'
BG='#000000';FG='#e9edf2';GRAY='#343a40';CYAN='#68c9d6';GOLD='#e9c46a'
f=lambda n:ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',n)
im=Image.new('RGB',(1440,480),BG);d=ImageDraw.Draw(im)
end=1792281900
fixtures=[('24h',86400,[(0,10000,False),(7200,9450,False),(18000,8800,False),(42000,7123,True),(64000,6000,False),(86400,4000,False)],3),
          ('7d',604800,[(0,10000,False),(86400,8800,False),(172800,5600,False),(350000,9700,True),(518400,6400,False),(604800,3200,False)],0),
          ('7d',604800,[],-1)]
for panel,(mode,span,points,chosen) in enumerate(fixtures):
    ox=panel*480;px=ox+20;py=352 # page origin y52 + chart y300
    d.text((px,20),'o  3     84%',font=f(20),fill=FG)
    for i,t in enumerate(('30d','24h')):
        x=px+i*224;d.rounded_rectangle((x,52,x+214,100),10,fill=('#286999' if (mode=='24h')==(i==1) else '#15191f'))
        d.text((x+84,64),t,font=f(16),fill=FG)
    d.text((px,106),'10/11 - 10/18',font=f(16),fill=FG)
    count=24 if mode=='24h' else 30
    for i in range(count):
        x=px+(i%6)*73+3;y=128+(i//6)*44
        d.rounded_rectangle((x,y,x+66,y+40),7,fill=('#234669' if mode=='24h' else '#24543e'))
        d.text((x+23,y+1),f'{i%24:02}',font=f(13),fill=FG)
        d.text((x+17,y+18),'2.5K',font=f(13),fill=FG)
    d.text((px,py-1),'100%',font=f(12),fill=FG);d.text((px+8,py+44),'0%',font=f(12),fill=FG)
    d.line((px+32,py+6,px+32,py+54),fill=GRAY,width=2);d.line((px+32,py+54,px+428,py+54),fill=GRAY,width=2)
    previous=None
    for i,(t,bp,gap) in enumerate(points):
        x=px+32+t*396//span;y=py+54-bp*48//10000
        if previous and not gap:d.line((*previous,x,y),fill=CYAN,width=2)
        d.ellipse((x-2,y-2,x+1,y+1),fill=GOLD if gap else CYAN)
        if i==chosen:
            d.line((x,max(py+2,y-7),x,min(py+58,y+7)),fill=GOLD,width=2)
            d.ellipse((x-4,y-4,x+4,y+4),outline=GOLD,width=2)
        previous=(x,y)
    hint=mode+' --'
    if chosen>=0:
        t,bp,_=points[chosen]
        time=datetime.fromtimestamp(end-span+t,timezone(timedelta(hours=8))).strftime('%H:%M' if mode=='24h' else '%m/%d %H:%M')
        hint=f'{mode} {time}  {bp/100:g}%'
    d.text((px+34,py+62),hint,font=f(12),fill=FG)
    d.text((px+392,py+62),'NOW',font=f(12),fill=FG)
    # Independent token detail at page y380 = screen y432.
    d.text((px+12,438),"10/18  2.5K tokens / observed",font=f(12),fill=FG)
    if panel:d.line((ox,0,ox,479),fill=GRAY)
OUT.mkdir(parents=True,exist_ok=True)
im.save(OUT/'trend-touch-preview.png')
print(OUT/'trend-touch-preview.png')
