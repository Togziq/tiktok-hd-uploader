"""Draws assets/icon.ico: a play button with TikTok-style cyan/pink offset."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"

SIZE = 256


def triangle(offset_x, offset_y):
    return [(92 + offset_x, 66 + offset_y), (92 + offset_x, 190 + offset_y), (196 + offset_x, 128 + offset_y)]


img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)
draw.rounded_rectangle((8, 8, SIZE - 8, SIZE - 8), radius=56, fill=(18, 18, 22, 255))
draw.polygon(triangle(-7, -5), fill=(37, 244, 238, 255))
draw.polygon(triangle(7, 5), fill=(254, 44, 85, 255))
draw.polygon(triangle(0, 0), fill=(255, 255, 255, 255))
draw.text((SIZE - 78, SIZE - 64), "HD", fill=(255, 255, 255, 255), font_size=40)
img.save(OUT.with_suffix(".png"))  # for the README, GitHub doesn't render .ico reliably
img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
