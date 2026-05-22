from PIL import Image, ImageDraw, ImageFont, ImageFilter
import math

SIZE = 1024
BG = (18, 19, 26)       # #12131a
NEON = (0, 220, 255)    # cyan neon
NEON2 = (160, 0, 255)   # purple accent

img = Image.new("RGBA", (SIZE, SIZE), BG + (255,))
draw = ImageDraw.Draw(img)

# --- rounded rect background ---
def rounded_rect(d, xy, r, fill):
    x0, y0, x1, y1 = xy
    d.rectangle([x0+r, y0, x1-r, y1], fill=fill)
    d.rectangle([x0, y0+r, x1, y1-r], fill=fill)
    d.ellipse([x0, y0, x0+2*r, y0+2*r], fill=fill)
    d.ellipse([x1-2*r, y0, x1, y0+2*r], fill=fill)
    d.ellipse([x0, y1-2*r, x0+2*r, y1], fill=fill)
    d.ellipse([x1-2*r, y1-2*r, x1, y1], fill=fill)

# subtle inner card
rounded_rect(draw, [80, 80, 944, 944], 120, (22, 24, 34, 255))

# --- glow layers (drawn as blurred circles / ellipses) ---
def add_glow(base, color, center, radius, alpha):
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy = center
    for r in range(radius, 0, -8):
        a = int(alpha * (1 - r / radius) ** 1.5)
        gd.ellipse([cx-r, cy-r, cx+r, cy+r], fill=color + (a,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius // 3))
    base.alpha_composite(glow)

add_glow(img, NEON,  (380, 512), 280, 180)
add_glow(img, NEON2, (644, 512), 220, 140)

# --- text: try system fonts, fallback to default ---
TEXT = "SM"
font = None
font_size = 480

candidates = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNSDisplay.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]
for path in candidates:
    try:
        font = ImageFont.truetype(path, font_size)
        break
    except Exception:
        continue

if font is None:
    font = ImageFont.load_default()

# measure text
bb = draw.textbbox((0, 0), TEXT, font=font)
tw, th = bb[2] - bb[0], bb[3] - bb[1]
tx = (SIZE - tw) // 2 - bb[0]
ty = (SIZE - th) // 2 - bb[1]

# --- neon glow layers for text ---
for blur, alpha in [(32, 90), (18, 140), (8, 200), (3, 230)]:
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.text((tx, ty), TEXT, font=font, fill=NEON + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    img.alpha_composite(layer)

# --- solid white-cyan text on top ---
draw2 = ImageDraw.Draw(img)
draw2.text((tx, ty), TEXT, font=font, fill=(210, 255, 255, 255))

# --- thin neon border ring ---
for i, (col, w) in enumerate([(NEON2, 6), (NEON, 3)]):
    offset = 60 + i * 4
    draw2.rounded_rectangle(
        [offset, offset, SIZE - offset, SIZE - offset],
        radius=120 - i * 10,
        outline=col + (180,),
        width=w,
    )

# --- small "motion" tagline ---
tag_size = 52
tag_font = None
for path in candidates:
    try:
        tag_font = ImageFont.truetype(path, tag_size)
        break
    except Exception:
        continue
if tag_font is None:
    tag_font = ImageFont.load_default()

tag = "MOTION"
tb = draw2.textbbox((0, 0), tag, font=tag_font)
tw2 = tb[2] - tb[0]
draw2.text(((SIZE - tw2) // 2 - tb[0], 820), tag, font=tag_font, fill=NEON + (200,))

# final save
out = img.convert("RGB")   # strip alpha for PNG compatibility with macOS
out.save("icon.png", "PNG")
print("Saved icon.png")
