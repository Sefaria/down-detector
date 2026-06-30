"""
Regenerate the social-preview (Open Graph) image at 1200x630.

Dev tool only — not needed at runtime, so Pillow is not a project dependency.
Run it when the branding changes:

    pip install Pillow
    python scripts/generate_og_image.py

Output: monitoring/static/img/og-image.png (light theme matching the status
page: warm off-white, the category-color accent band, navy serif wordmark,
gold rule). Tries common serif/sans fonts across platforms.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
BG = (251, 251, 250)     # #FBFBFA
NAVY = (24, 52, 93)      # #18345D
GOLD = (204, 180, 121)   # #CCB479
MUTED = (102, 102, 100)  # #666664
FAINT = (138, 138, 135)  # #8A8A87
RAINBOW = [
    "#00505E", "#5698B4", "#CCB37C", "#5B9370", "#823241",
    "#5A4474", "#AD4F66", "#7285A6", "#00807E", "#4872B3",
]

# Candidate font files per role, in preference order (Windows, then Linux).
SERIF_BOLD = ["georgiab.ttf", "C:/Windows/Fonts/georgiab.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"]
SERIF = ["georgia.ttf", "C:/Windows/Fonts/georgia.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"]
SANS = ["arial.ttf", "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def load(candidates, size):
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Top category-color accent band (the signature Sefaria motif).
    seg = W / len(RAINBOW)
    for i, hexc in enumerate(RAINBOW):
        d.rectangle([int(i * seg), 0, int((i + 1) * seg), 16], fill=hexc)

    f_title = load(SERIF_BOLD, 92)
    f_sub = load(SERIF, 40)
    f_tag = load(SANS, 28)

    def centered(text, font, y, fill):
        box = d.textbbox((0, 0), text, font=font)
        d.text(((W - (box[2] - box[0])) / 2, y), text, font=font, fill=fill)

    centered("Sefaria Status", f_title, 232, NAVY)
    rule_w = 240
    d.rectangle([(W - rule_w) / 2, 360, (W + rule_w) / 2, 364], fill=GOLD)
    centered("Real-time status of Sefaria's services", f_sub, 392, MUTED)
    centered("is sefaria down?  ·  status.sefaria.org", f_tag, 470, FAINT)

    out = Path(__file__).resolve().parent.parent / "monitoring/static/img/og-image.png"
    img.save(out, "PNG", optimize=True)
    print(f"wrote {out} ({W}x{H})")


if __name__ == "__main__":
    main()
