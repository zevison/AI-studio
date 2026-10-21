"""Render the original, code-defined Seedance video-frame/play identity."""
from pathlib import Path
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[1]
SVG='''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#18213b"/><stop offset="1" stop-color="#0b1221"/></linearGradient>
<linearGradient id="frame" x1="0" y1="1" x2="1" y2="0"><stop stop-color="#9362f4"/><stop offset="1" stop-color="#32d5df"/></linearGradient></defs>
<rect x="8" y="8" width="240" height="240" rx="56" fill="url(#bg)"/>
<rect x="47" y="57" width="162" height="142" rx="29" fill="none" stroke="url(#frame)" stroke-width="14"/>
<path d="M86 102V154L103 144V112Z" fill="#9eabcf"/>
<path d="M111 88V168L173 128Z" fill="#ffffff"/>
</svg>'''


def render(size=1024):
    scale=size/256
    image=Image.new('RGBA',(size,size));back=Image.new('RGBA',(size,size));frame=Image.new('RGBA',(size,size))
    for y in range(size):
        for x in range(size):
            t=(x+y)/(2*max(1,size-1));u=(x+size-1-y)/(2*max(1,size-1))
            back.putpixel((x,y),tuple(round(a+(b-a)*t) for a,b in zip((24,33,59),(11,18,33)))+(255,))
            frame.putpixel((x,y),tuple(round(a+(b-a)*u) for a,b in zip((147,98,244),(50,213,223)))+(255,))
    def box(values):return tuple(round(v*scale) for v in values)
    mask=Image.new('L',(size,size));d=ImageDraw.Draw(mask);d.rounded_rectangle(box((8,8,248,248)),radius=56*scale,fill=255)
    image.paste(back,(0,0),mask)
    mask=Image.new('L',(size,size));d=ImageDraw.Draw(mask)
    d.rounded_rectangle(box((40,50,216,206)),radius=36*scale,fill=255)
    d.rounded_rectangle(box((54,64,202,192)),radius=22*scale,fill=0)
    image.paste(frame,(0,0),mask)
    d=ImageDraw.Draw(image)
    d.polygon([box(p) for p in ((86,102),(86,154),(103,144),(103,112))],fill='#9eabcf')
    d.polygon([box(p) for p in ((111,88),(111,168),(173,128))],fill='white')
    return image.resize((256,256),Image.Resampling.LANCZOS)


if __name__=='__main__':
    destination=ROOT/'assets/branding';destination.mkdir(parents=True,exist_ok=True)
    image=render();image.save(destination/'seedance-studio.png')
    image.save(destination/'seedance-studio.ico',sizes=[(n,n) for n in (16,24,32,48,64,96,128,256)])
    (ROOT/'static/seedance-icon.svg').write_text(SVG+'\n',encoding='utf8')
