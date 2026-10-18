"""Rebuild original project artwork. Requires Pillow; no runtime dependency."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import shutil

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'docs/assets'
BRAND = ROOT / 'custom_components/comelit_native/brand'
APP = ROOT / 'addons/comelit_external_audio'
for path in (ASSETS, BRAND):
    path.mkdir(parents=True, exist_ok=True)

NAVY = '#102D38'
MINT = '#4CE0BB'
WHITE = '#F2FAF8'
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

def font(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)

def icon(size):
    im = Image.new('RGBA', (1024, 1024))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0,0,1023,1023),radius=220,fill=NAVY)
    d.rounded_rectangle((220,190,580,834),radius=42,fill='#193F4C',outline=WHITE,width=30)
    d.line((292,272,292,752),fill=MINT,width=22)
    d.ellipse((468,484,508,524),fill=WHITE)
    for x, y1, y2 in ((680,454,570),(764,374,650),(848,428,596)):
        d.rounded_rectangle((x-15,y1,x+15,y2),radius=15,fill=MINT)
    return im.resize((size,size),Image.Resampling.LANCZOS)

for size,suffix in ((256,''),(512,'@2x')):
    im=icon(size)
    for prefix in ('','dark_'):
        im.save(BRAND/f'{prefix}icon{suffix}.png',optimize=True)

svg='''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 1024 1024">
<rect width="1024" height="1024" rx="220" fill="#102D38"/>
<rect x="220" y="190" width="360" height="644" rx="42" fill="#193F4C" stroke="#F2FAF8" stroke-width="30"/>
<path d="M292 272V752" stroke="#4CE0BB" stroke-width="22"/>
<circle cx="488" cy="504" r="20" fill="#F2FAF8"/>
<path d="M680 469V555 M764 389V635 M848 443V581" stroke="#4CE0BB" stroke-width="30" stroke-linecap="round"/>
</svg>'''
(ASSETS/'icon.svg').write_text(svg+'\n')

for dark in (False,True):
    for scale,suffix in ((1,''),(2,'@2x')):
        im=Image.new('RGBA',(1120*scale,256*scale))
        im.alpha_composite(icon(256*scale),(0,0))
        d=ImageDraw.Draw(im)
        fg=WHITE if dark else NAVY
        d.text((300*scale,38*scale),'Comelit ViP',font=font(79*scale,True),fill=fg)
        d.text((304*scale,148*scale),'NATIVE  /  COMMUNITY',font=font(34*scale),fill='#268E7A' if not dark else MINT)
        im.save(BRAND/f'{"dark_" if dark else ""}logo{suffix}.png',optimize=True)

shutil.copy2(BRAND/'icon.png',APP/'icon.png')
shutil.copy2(BRAND/'logo.png',APP/'logo.png')
shutil.copy2(BRAND/'icon@2x.png',ASSETS/'icon.png')
im=Image.new('RGB',(1280,640),NAVY)
d=ImageDraw.Draw(im)
d.rounded_rectangle((906,-110,1470,470),radius=180,fill='#163B48')
d.rounded_rectangle((1030,220,1390,830),radius=140,outline='#245260',width=2)
im.paste(icon(188),(1006,94),icon(188))
d.text((72,60),'COMMUNITY PROJECT',font=font(21,True),fill=MINT)
d.text((68,140),'Comelit ViP',font=font(86,True),fill=WHITE)
d.text((70,242),'Native',font=font(86,True),fill=WHITE)
d.text((74,366),'Home Assistant  /  Loxone',font=font(32),fill='#B9D1D8')
for x,title,detail in ((72,'RING','Doorbell events'),(445,'OPEN','Door release'),(818,'TALK','Two-way audio')):
    d.rounded_rectangle((x,472,x+344,584),radius=18,fill='#1A404D')
    d.text((x+24,492),title,font=font(23,True),fill=MINT)
    d.text((x+24,532),detail,font=font(21),fill=WHITE)
im.save(ASSETS/'banner.png',optimize=True)
print('Original icons, light/dark wordmarks and README banner generated.')
