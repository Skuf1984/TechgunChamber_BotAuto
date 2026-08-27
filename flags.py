"""Flag icons (drawn with PIL) for the language switcher."""

from PIL import Image, ImageDraw


def make_ru_flag(w=36, h=24):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    t = h // 3
    d.rectangle([0, 0, w, t], fill=(240, 240, 240))
    d.rectangle([0, t, w, 2 * t], fill=(0, 57, 166))
    d.rectangle([0, 2 * t, w, h], fill=(213, 43, 30))
    return img


def make_us_flag(w=36, h=24):
    img = Image.new("RGB", (w, h), (240, 240, 240))
    d = ImageDraw.Draw(img)
    stripes = 13
    sh = h / stripes
    for i in range(stripes):
        if i % 2 == 0:
            d.rectangle([0, int(i * sh), w, int((i + 1) * sh)], fill=(178, 34, 52))
    ch = int(h * 7 / 13)
    cw = int(w * 2 / 5)
    d.rectangle([0, 0, cw, ch], fill=(60, 59, 110))
    for r in range(3):
        for c in range(4):
            x = int(cw * (c + 0.5) / 4)
            y = int(ch * (r + 0.5) / 3)
            d.ellipse([x - 1, y - 1, x + 1, y + 1], fill=(255, 255, 255))
    return img
