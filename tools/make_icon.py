"""Generate the application icon (PNG + multi-size ICO) with Pillow.

Design: dark rounded square, a blue motor/servo ring with a 'pulse' trace and a stethoscope
style accent - reads as 'motion controller diagnostics'.
"""
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "pmacdoctor", "ui", "resources")


def draw(size: int) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = s * 0.22
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=r, fill=(26, 26, 25, 255))
    # ring (motor)
    cx, cy = s * 0.5, s * 0.5
    R = s * 0.33
    w = max(2, int(s * 0.085))
    d.ellipse((cx - R, cy - R, cx + R, cy + R), outline=(57, 135, 229, 255), width=w)
    # rotor dot
    rr = s * 0.07
    d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=(57, 135, 229, 255))
    # pulse trace across (health)
    pw = max(2, int(s * 0.06))
    y0 = cy
    pts = [(s * 0.08, y0), (s * 0.30, y0), (s * 0.37, y0 - s * 0.16), (s * 0.45, y0 + s * 0.20),
           (s * 0.53, y0 - s * 0.10), (s * 0.58, y0), (s * 0.92, y0)]
    d.line(pts, fill=(12, 163, 12, 255), width=pw, joint="curve")
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    big = draw(512)
    big.save(os.path.join(OUT, "icon.png"))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [draw(z) for z in sizes]
    imgs[0].save(os.path.join(OUT, "icon.ico"), format="ICO", sizes=[(z, z) for z in sizes],
                 append_images=imgs[1:])
    print("icons written to", OUT)


if __name__ == "__main__":
    sys.exit(main())
