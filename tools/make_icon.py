"""Icône de Claude Pilot : propositions dessinées en grand puis réduites, pour rester nettes à toutes les tailles.

Usage : .venv\\Scripts\\python.exe tools\\make_icon.py [A|B|C|D] [--apercu fichier.png]
  D · Onde : l'onde de voix seule (icône actuelle, depuis la 1.0.10)
  A · Viseur : cadre de capture d'écran + onde de voix (jusqu'à la 1.0.9)
  B · Bulle : bulle de parole + curseur qui montre
  C · Avion : avion en papier (piloter, envoyer) + ondes
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "claudepilot" / "assets" / "icon.ico"
S = 1024  # taille du dessin, réduit ensuite
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
WHITE = (255, 255, 255, 255)


def background(top_left: tuple[int, int, int], bottom_right: tuple[int, int, int]) -> Image.Image:
    """Carré arrondi en dégradé diagonal, avec un léger reflet en haut."""
    y, x = np.mgrid[0:S, 0:S]
    t = ((x + y) / (2 * (S - 1)))[..., None]
    rgb = np.array(top_left) * (1 - t) + np.array(bottom_right) * t
    gloss = np.clip(1 - y / (S * 0.55), 0, 1)[..., None] * 0.16  # reflet blanc qui s'efface vers le milieu
    rgb = rgb * (1 - gloss) + 255 * gloss
    fill = Image.fromarray(np.dstack([rgb.astype("uint8"), np.full((S, S), 255, "uint8")]), "RGBA")
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((32, 32, S - 32, S - 32), radius=232, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(fill, (0, 0), mask)
    return out


def bars(draw: ImageDraw.ImageDraw, cx: float, cy: float, heights, width: float, gap: float, color) -> None:
    """Onde de voix : barres arrondies, la plus haute au centre."""
    x = cx - (len(heights) * width + (len(heights) - 1) * gap) / 2
    for h in heights:
        draw.rounded_rectangle((x, cy - h / 2, x + width, cy + h / 2), radius=width / 2, fill=color)
        x += width + gap


def viewfinder(draw: ImageDraw.ImageDraw, box, arm: float, width: int, color) -> None:
    """Les quatre coins d'un cadre de capture, bouts arrondis."""
    x0, y0, x1, y1 = box
    r = width / 2
    for pts in (((x0, y0 + arm), (x0, y0), (x0 + arm, y0)), ((x1 - arm, y0), (x1, y0), (x1, y0 + arm)),
                ((x1, y1 - arm), (x1, y1), (x1 - arm, y1)), ((x0 + arm, y1), (x0, y1), (x0, y1 - arm))):
        draw.line(pts, fill=color, width=width, joint="curve")
        for px, py in (pts[0], pts[1], pts[2]):
            draw.ellipse((px - r, py - r, px + r, py + r), fill=color)


def variant_a(small: bool) -> Image.Image:
    img = background((67, 56, 202), (147, 51, 234))
    draw = ImageDraw.Draw(img)
    if small:  # en 16-32 px, les coins du cadre deviennent de la bouillie : on garde l'onde, plus grosse
        bars(draw, 512, 512, [260, 470, 640, 470, 260], 104, 56, WHITE)
    else:
        viewfinder(draw, (232, 232, 792, 792), 150, 62, WHITE)
        bars(draw, 512, 512, [130, 250, 350, 250, 130], 54, 30, WHITE)
    return img


def variant_b(small: bool) -> Image.Image:
    img = background((14, 165, 233), (29, 78, 216))
    draw = ImageDraw.Draw(img)
    bubble = (180, 210, 844, 690) if not small else (150, 200, 874, 740)
    draw.rounded_rectangle(bubble, radius=170, fill=WHITE)
    tail_y = bubble[3] - 30
    draw.polygon([(bubble[0] + 120, tail_y), (bubble[0] + 70, tail_y + 180), (bubble[0] + 300, tail_y + 10)], fill=WHITE)
    cy = (bubble[1] + bubble[3]) / 2
    if small:
        bars(draw, 512, cy, [150, 290, 400, 290, 150], 72, 40, (29, 78, 216, 255))
    else:
        bars(draw, 470, cy, [100, 190, 270, 190, 100], 46, 28, (29, 78, 216, 255))
        tip = np.array([600, 500])
        arrow = [(0, 0), (0, 330), (84, 252), (150, 388), (208, 362), (142, 230), (248, 230)]
        draw.polygon([tuple(tip + np.array(p)) for p in arrow], fill=(15, 23, 42, 255), outline=WHITE, width=24)
    return img


def variant_c(small: bool) -> Image.Image:
    img = background((251, 146, 60), (225, 29, 72))
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))  # calque à part : les blancs translucides se fondent dans le fond
    draw = ImageDraw.Draw(layer)
    shift = np.array([-20, 30]) if small else np.array([40, -10])
    tip, wing, fold, tail = (np.array(p) + shift for p in ((800, 230), (200, 500), (470, 590), (560, 810)))
    draw.polygon([tuple(tip), tuple(wing), tuple(fold)], fill=WHITE)
    draw.polygon([tuple(tip), tuple(fold), tuple(tail)], fill=(255, 255, 255, 205))
    if not small:
        for radius, alpha in ((150, 230), (250, 150)):
            box = (230 - radius, 790 - radius, 230 + radius, 790 + radius)
            draw.arc(box, start=275, end=355, fill=(255, 255, 255, alpha), width=44)
    img.alpha_composite(layer)
    return img


def variant_d(small: bool) -> Image.Image:
    img = background((67, 56, 202), (147, 51, 234))
    draw = ImageDraw.Draw(img)
    if small:  # même onde, barres plus épaisses pour rester lisibles en 16-32 px
        bars(draw, 512, 512, [260, 470, 640, 470, 260], 104, 56, WHITE)
    else:
        bars(draw, 512, 512, [230, 400, 560, 400, 230], 86, 52, WHITE)
    return img


VARIANTS = {"D": ("Onde", variant_d), "A": ("Viseur", variant_a), "B": ("Bulle", variant_b), "C": ("Avion", variant_c)}


def sized(variant: str) -> dict[int, Image.Image]:
    _name, draw = VARIANTS[variant]
    big, small = draw(False), draw(True)
    return {size: (small if size <= 32 else big).resize((size, size), Image.LANCZOS) for size in SIZES}


def save_ico(variant: str, path: Path = ICON) -> None:
    images = sized(variant)
    path.parent.mkdir(parents=True, exist_ok=True)
    images[256].save(path, sizes=[(s, s) for s in SIZES], append_images=[images[s] for s in SIZES if s != 256])
    images[256].save(ROOT / "icon.png")  # en-tête du README


def preview(path: Path) -> None:
    """Planche de comparaison : chaque icône en grand puis en petit, sur fond sombre et sur fond clair."""
    try:
        font = ImageFont.truetype("segoeuib.ttf", 26)
    except OSError:
        font = ImageFont.load_default(size=26)
    row_h, width = 330, 1180
    sheet = Image.new("RGB", (width, row_h * len(VARIANTS) + 20), (17, 17, 19))
    draw = ImageDraw.Draw(sheet)
    for i, (key, (name, _fn)) in enumerate(VARIANTS.items()):
        y = 20 + i * row_h
        images = sized(key)
        draw.text((24, y), f"{key} · {name}", font=font, fill=(232, 232, 234))
        sheet.paste(images[256], (24, y + 44), images[256])
        light = Image.new("RGB", (300, 284), (243, 244, 246))
        light.paste(images[256], (22, 14), images[256])
        sheet.paste(light, (300, y + 30))
        x = 640
        for size in (64, 48, 32, 24, 16):
            for dy, bg in ((44, (17, 17, 19)), (164, (243, 244, 246))):
                tile = Image.new("RGB", (size + 24, size + 24), bg)
                tile.paste(images[size], (12, 12), images[size])
                sheet.paste(tile, (x, y + dy))
            x += size + 44
    sheet.save(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("variant", nargs="?", choices=list(VARIANTS))
    parser.add_argument("--apercu", type=Path)
    args = parser.parse_args()
    if args.apercu:
        preview(args.apercu)
        print("Aperçu :", args.apercu)
    if args.variant:
        save_ico(args.variant)
        print(f"Icône {args.variant} · {VARIANTS[args.variant][0]} enregistrée : {ICON}")
