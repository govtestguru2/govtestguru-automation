"""lesson_video.py — Lesson series ka engine (script JSON -> video -> upload).

Pipeline (Guide Section 4):
  script JSON padho -> PIL se 1920x1080 slides (poori slide EK SAATH, koi
  animation nahi) -> har scene ki awaaz live_tts (Leda) se, narration text
  hindi_pronunciation.prepare_tts_text() se saaf karke -> ffmpeg se video
  jodo -> youtube_upload se upload (default: private) -> state files
  ATOMIC roop se update karo.

State files (repo root par):
  lesson_state.json : {"next": N} — agli video ka number
  lesson_next.txt   : agli script ka path (pointer)
  lesson_latest.txt : aakhri bani video ka link

STATE-LOSS TRAP (Guide 5.3): run chalte samay repo me koi aur commit mat
karo, warna state wala push chupchaap kho sakta hai aur wahi video dobara
banegi. Har run ke baad state/latest ko raw/API se verify karo.

Koi bhi key is file me nahi hai — sab env/GitHub Secrets se aati hain.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "lesson_state.json")
NEXT_FILE = os.path.join(BASE_DIR, "lesson_next.txt")
LATEST_FILE = os.path.join(BASE_DIR, "lesson_latest.txt")
WORK_DIR = os.path.join(BASE_DIR, "build")

SLIDE_W, SLIDE_H = 1920, 1080
MAX_POINTS = 5

sys.path.insert(0, BASE_DIR)
from hindi_pronunciation import prepare_tts_text  # noqa: E402


# ---------------------------------------------------------------------------
# Atomic file helpers
# ---------------------------------------------------------------------------
def atomic_write(path: str, content: str) -> None:
    """Pehle temp file me likho, phir os.replace se badlo — aadhi file kabhi nahi."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    return {"next": 1}


def read_next_pointer() -> str:
    with open(NEXT_FILE, encoding="utf-8") as fh:
        return fh.read().strip()


# ---------------------------------------------------------------------------
# Fonts — Devanagari aur Latin ke ALAG slot (Guide 4.2)
# ---------------------------------------------------------------------------
_FONT_CANDIDATES = {
    # Repo ke andar bundled fonts SABSE PEHLE — GitHub runner par Devanagari
    # font system me nahi milta, isiliye pehle test video me Hindi blank aaya
    # tha (15 Nov 2026, Priya: "Hindi lekha hua nhi dikh raha").
    "deva_bold": [
        os.path.join(BASE_DIR, "fonts", "NotoSansDevanagari-Bold.ttf"),
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSerifDevanagari-Bold.ttf",
    ],
    "deva": [
        os.path.join(BASE_DIR, "fonts", "NotoSansDevanagari-Regular.ttf"),
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari.ttf",
    ],
    "latin_bold": [
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "latin": [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}


_FONT_LOGGED: set = set()


def _find_font(kind: str) -> str | None:
    for path in _FONT_CANDIDATES[kind]:
        if os.path.exists(path):
            return path
    return None


def _has_devanagari(text: str) -> bool:
    return any("\u0900" <= ch <= "\u097F" for ch in text)


def get_font(text: str, size: int, bold: bool = False):
    """Text ki lipi ke hisaab se sahi font slot chuno."""
    from PIL import ImageFont

    if _has_devanagari(text):
        kind = "deva_bold" if bold else "deva"
    else:
        kind = "latin_bold" if bold else "latin"
    path = _find_font(kind) or _find_font("latin") or _find_font("deva")
    if kind not in _FONT_LOGGED:
        _FONT_LOGGED.add(kind)
        print(f"Font [{kind}]: {path}")
    if path is None:
        return ImageFont.load_default()
    return ImageFont.truetype(path, size)


# ---------------------------------------------------------------------------
# Slides (PIL) — poori slide ek saath, koi typing/reveal animation nahi
# ---------------------------------------------------------------------------
# Design 15 — "News Bulletin" (Priya ne 15 Nov 2026 ko FINAL chuna; 19 ke baad badla)
BG = (245, 246, 250)
NAVY = (13, 42, 110)
RED = (200, 30, 45)
HEAD_COLOR = (16, 26, 54)
POINT_COLOR = (44, 50, 68)
ACCENT = (246, 197, 107)
TICKER = "CTET • B.Ed • UGC NET • JTET — सरल हिंदी में पूरी तैयारी"


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


# --- Mixed-script rendering (15 Nov 2026 fix) -------------------------------
# Ek hi line me Hindi + English ho to poori line ek font se mat kheecho:
# Noto Sans Devanagari me Latin akshar NAHI hain (English shabd dabbe □ ban
# jate the) aur Latin font me Hindi nahi hai. Isiliye line ko lipi ke hisaab
# se tukdo (runs) me baant kar har tukda usi ke font se banao.
def _split_runs(text: str) -> list[tuple[str, bool]]:
    runs: list[tuple[str, bool]] = []
    cur, cur_deva = "", None
    for ch in text:
        deva = "\u0900" <= ch <= "\u097f"
        if cur_deva is None or deva == cur_deva:
            cur += ch
            cur_deva = deva
        else:
            runs.append((cur, bool(cur_deva)))
            cur, cur_deva = ch, deva
    if cur:
        runs.append((cur, bool(cur_deva)))
    return runs


def _font_for(deva: bool, size: int, bold: bool = False):
    return get_font("अ" if deva else "A", size, bold=bold)


def _mixed_width(draw, text: str, size: int, bold: bool = False) -> float:
    return sum(
        draw.textlength(seg, font=_font_for(deva, size, bold))
        for seg, deva in _split_runs(text)
    )


def _wrap_mixed(draw, text: str, size: int, bold: bool, max_width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if _mixed_width(draw, trial, size, bold) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _draw_mixed(draw, xy, text: str, size: int, bold: bool, fill) -> None:
    x, y = xy
    for seg, deva in _split_runs(text):
        f = _font_for(deva, size, bold)
        draw.text((x, y), seg, font=f, fill=fill)
        x += draw.textlength(seg, font=f)


def _render_diagram(spec: dict):
    """Script ke 'diagram' data se panel image banao — labels script se hi
    aate hain, isiliye image hamesha script se match karti hai (Priya ki
    shart: 'script ke hisab se image match kr raha hai ya nahi' check).
    spec: {"title": str, "boxes": [str, ...]}  (adhiktam 5 boxes)"""
    from PIL import Image, ImageDraw

    width = 800
    pad = 28
    title_h = 84
    label_size = 40
    boxes = [str(b) for b in spec.get("boxes", [])][:5]
    title = str(spec.get("title", ""))

    # Pehle ek scratch draw par har box ki unchai naapo
    scratch = ImageDraw.Draw(Image.new("RGB", (width, 10)))
    wrapped = []
    heights = []
    for b in boxes:
        lines = _wrap_mixed(scratch, b, label_size, True, width - 2 * pad - 40)
        wrapped.append(lines)
        heights.append(len(lines) * 56 + 30)
    arrow_h = 34
    total_h = title_h + pad + sum(heights) + arrow_h * max(0, len(boxes) - 1) + pad

    img = Image.new("RGB", (width, total_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, width - 2, total_h - 2], radius=24, outline=NAVY, width=4)
    # Title bar
    draw.rounded_rectangle([2, 2, width - 2, title_h], radius=24, fill=NAVY)
    draw.rectangle([2, title_h - 24, width - 2, title_h], fill=NAVY)
    t_lines = _wrap_mixed(draw, title, 38, True, width - 2 * pad)
    ty = (title_h - len(t_lines) * 52) // 2 + 4
    for line in t_lines:
        lw = _mixed_width(draw, line, 38, True)
        _draw_mixed(draw, ((width - lw) / 2, ty), line, 38, True, (255, 255, 255))
        ty += 52
    # Boxes + teer (arrow)
    y = title_h + pad
    for i, (lines, bh) in enumerate(zip(wrapped, heights)):
        draw.rounded_rectangle([pad, y, width - pad, y + bh], radius=16,
                               fill=(233, 238, 252), outline=NAVY, width=2)
        ly = y + (bh - len(lines) * 56) // 2 + 6
        for line in lines:
            lw = _mixed_width(draw, line, label_size, True)
            _draw_mixed(draw, ((width - lw) / 2, ly), line, label_size, True, HEAD_COLOR)
            ly += 56
        y += bh
        if i < len(boxes) - 1:
            cx = width // 2
            draw.polygon([(cx - 14, y + 6), (cx + 14, y + 6), (cx, y + arrow_h - 4)], fill=RED)
            y += arrow_h
    return img


def _topic_from_title(yt_title: str) -> str:
    """yt_title se topic ka naam nikalo — 'B.Ed पाठ 5 | किशोरावस्था (...) |
    संपूर्ण पाठ' -> 'किशोरावस्था (...)'. Priya (15 Nov 2026): slide ke neeche
    sirf wahi topic dikhe jiski padhai ho rahi hai."""
    parts = [p.strip() for p in str(yt_title).split("|")]
    parts = [p for p in parts if p]
    if parts and "पाठ" in parts[0]:
        parts = parts[1:]
    if parts and parts[-1] == "संपूर्ण पाठ":
        parts = parts[:-1]
    return " | ".join(parts).strip()


def build_slide(scene: dict, out_path: str, brand: str = "GovTestGuru",
                topic: str = "") -> str:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (SLIDE_W, SLIDE_H), BG)
    draw = ImageDraw.Draw(img)
    # Design 15 frame: upar navy patti (brand + laal pill), neeche navy ticker patti
    draw.rectangle([0, 0, SLIDE_W, 130], fill=NAVY)
    _draw_mixed(draw, (90, 42), brand, 40, True, (255, 255, 255))
    draw.rounded_rectangle([SLIDE_W - 420, 34, SLIDE_W - 90, 96], radius=31, fill=RED)
    _draw_mixed(draw, (SLIDE_W - 388, 48), "रोज़ सुबह 6 बजे", 30, True, (255, 255, 255))

    # Diagram (script se banti image) — ho to wahi lagegi; warna optional photo
    text_right = SLIDE_W - 90
    diagram = scene.get("diagram")
    if diagram:
        panel = _render_diagram(diagram)
        img.paste(panel, (SLIDE_W - 90 - panel.width, 190))
        text_right = SLIDE_W - 90 - panel.width - 60

    # Optional photo — SIRF pehle se verify ki hui img_file; na mile to bina photo
    img_file = scene.get("img_file")
    if img_file and not diagram:
        img_path = os.path.join(BASE_DIR, img_file)
        if os.path.exists(img_path):
            try:
                photo = Image.open(img_path).convert("RGB")
                photo.thumbnail((560, 560))
                img.paste(photo, (SLIDE_W - 90 - photo.width, 240))
                text_right = SLIDE_W - 90 - photo.width - 60
            except Exception as exc:  # photo kharab ho to slide bina photo ke
                print(f"WARNING: img_file load nahi hua ({img_file}): {exc}")
        else:
            print(f"WARNING: img_file mila nahi, bina photo slide banegi: {img_file}")

    # Headline — Priya (15 Nov 2026): topic ka font thoda chota (bilingual
    # lambe titles bhi aaram se fit hon), isliye 86 -> 64
    y = 210
    for line in _wrap_mixed(draw, scene["head"], 64, True, text_right - 90):
        _draw_mixed(draw, (90, y), line, 64, True, HEAD_COLOR)
        y += 82
    y += 30

    # Points — laal khadi patti ke saath
    point_font_size = 54
    for point in scene.get("points", []):
        lines = _wrap_mixed(draw, point, point_font_size, False, text_right - 90 - 50)
        draw.rectangle([90, y + 6, 102, y + 58], fill=RED)
        for line in lines:
            _draw_mixed(draw, (140, y), line, point_font_size, False, POINT_COLOR)
            y += 68
        y += 30

    # Neeche ki patti — Priya (15 Nov 2026): exam-naamo ki list NAHI,
    # sirf us topic ka naam jiski padhai ho rahi hai
    draw.rectangle([0, SLIDE_H - 90, SLIDE_W, SLIDE_H], fill=NAVY)
    _draw_mixed(draw, (90, SLIDE_H - 64), topic or TICKER, 34, True, (255, 255, 255))

    img.save(out_path)
    return out_path


def _split_text_chunks(text: str, max_words: int = 60) -> list[str]:
    """Narration text ko vakya-seemaon par todkar chunks banao — har chunk
    ek text-slide par pura likha hua explanation banega (Priya, 22 Nov 2026:
    student sunkar notes nahi likhega, sab likha bhi hona chahiye)."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return []
    sentences = [s.strip() for s in
                 re.split(r"(?<=[।.!?])\s+", text) if s.strip()]
    chunks, current = [], ""
    for sent in sentences:
        words = sent.split()
        if len(words) > max_words:  # ek hi vakya bahut lamba ho to tod do
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(words), max_words):
                chunks.append(" ".join(words[i:i + max_words]))
            continue
        candidate = f"{current} {sent}".strip() if current else sent
        if len(candidate.split()) <= max_words:
            current = candidate
        else:
            chunks.append(current)
            current = sent
    if current:
        chunks.append(current)
    return chunks


def build_text_slide(head: str, chunk: str, out_path: str,
                     topic: str = "") -> str:
    """Design 15 frame ke andar pura likha hua explanation — notes-style
    slide: upar chhota head, neeche poora text bade aksharon me."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (SLIDE_W, SLIDE_H), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, SLIDE_W, 130], fill=NAVY)
    _draw_mixed(draw, (90, 42), "GovTestGuru", 40, True, (255, 255, 255))
    draw.rounded_rectangle([SLIDE_W - 420, 34, SLIDE_W - 90, 96], radius=31, fill=RED)
    _draw_mixed(draw, (SLIDE_W - 388, 48), "रोज़ सुबह 6 बजे", 30, True, (255, 255, 255))

    # Head — chhota, adhiktam 2 line
    y = 172
    for line in _wrap_mixed(draw, head, 44, True, SLIDE_W - 180)[:2]:
        _draw_mixed(draw, (90, y), line, 44, True, HEAD_COLOR)
        y += 58
    y += 16
    draw.rectangle([90, y, SLIDE_W - 90, y + 6], fill=RED)
    y += 44

    # Body — pura chunk; fit na ho to font chhota karo (min 36)
    body_bottom = SLIDE_H - 130
    size = 48
    while size > 36:
        lines = _wrap_mixed(draw, chunk, size, False, SLIDE_W - 180)
        if y + len(lines) * (size + 20) <= body_bottom:
            break
        size -= 4
    line_h = size + 20
    for line in _wrap_mixed(draw, chunk, size, False, SLIDE_W - 180):
        if y + line_h > body_bottom:
            break
        _draw_mixed(draw, (90, y), line, size, False, POINT_COLOR)
        y += line_h

    draw.rectangle([0, SLIDE_H - 90, SLIDE_W, SLIDE_H], fill=NAVY)
    _draw_mixed(draw, (90, SLIDE_H - 64), topic or TICKER, 34, True, (255, 255, 255))

    img.save(out_path)
    return out_path


# ---------------------------------------------------------------------------
# Thumbnail — Design B "Dark Premium" (Priya ne 18 Nov 2026 ko FINAL chuna;
# sample: your_files/thumbnail-designs/thumb-design-B.png se hu-ba-hu geometry)
# ---------------------------------------------------------------------------
THUMB_DARKNAVY = (9, 18, 48)
THUMB_GOLD = ACCENT  # (246, 197, 107)
THUMB_BRAND = "Govt Test Guru"


def _thumb_fit_title(draw, text, max_width, max_lines, start, minimum=40):
    """Title ko max_lines ke andar fit karne ke liye font size ghatao."""
    size = start
    while size > minimum:
        lines = _wrap_mixed(draw, text, size, True, max_width)
        if len(lines) <= max_lines:
            return size, lines
        size -= 4
    return size, _wrap_mixed(draw, text, size, True, max_width)


def _thumb_centered(draw, y, text, size, bold, fill, width=1280):
    w = _mixed_width(draw, text, size, bold)
    _draw_mixed(draw, ((width - w) / 2, y), text, size, bold, fill)
    return w


def _thumb_pill(draw, cx, y, text, size, fill, text_fill, pad_x=34, h=None):
    tw = _mixed_width(draw, text, size, True)
    h = h or (size + 30)
    x0 = cx - tw / 2 - pad_x
    x1 = cx + tw / 2 + pad_x
    draw.rounded_rectangle([x0, y, x1, y + h], radius=h // 2, fill=fill)
    _draw_mixed(draw, (cx - tw / 2, y + (h - size) / 2 - size * 0.14),
                text, size, True, text_fill)
    return x1 - x0


def _thumb_exam_tag(spec: dict) -> str:
    """Gold pill ka exam tag — script ki series ke hisaab se. spec me 'tag'
    ho to wahi; warna brand_line/yt_title se: MMHAPU -> 'MMHAPU PYQ',
    AKU -> 'AKU PYQ', CTET -> 'CTET', B.Ed -> 'B.Ed'."""
    if spec.get("tag"):
        return str(spec["tag"])
    hay = f"{spec.get('brand_line', '')} {spec.get('yt_title', '')}"
    if "MMHAPU" in hay:
        return "MMHAPU PYQ"
    if "AKU" in hay:
        return "AKU PYQ"
    if "CTET" in hay:
        return "CTET"
    if "B.Ed" in hay:
        return "B.Ed"
    first = str(spec.get("brand_line", "")).split("•")[0].strip()
    return first or "GovTestGuru"


def _render_thumbnail(spec: dict, out_path: str) -> str:
    """Thumbnail (1280x720) — Design B 'Dark Premium' (Priya, 18 Nov 2026
    FINAL): gehra navy background, gold frame, gold pill me exam tag, uske
    neeche chapter_no gold me, beech me bada safed Hindi title, title ke
    neeche gold rekha, aur neeche laal patti par 'Govt Test Guru'.
    Purana chapter-list wala style (15 Nov 2026) poori tarah HATA diya gaya
    hai — ab har video ka thumbnail yahi ek design hoga; sirf exam tag,
    chapter_no aur title badlega.
    spec: script ka 'thumb' dict (title, chapter_no, brand_line/tag) —
    run() topic/yt_title bhi mila deta hai (title/tag ke fallback ke liye)."""
    from PIL import Image, ImageDraw

    W, H = 1280, 720
    img = Image.new("RGB", (W, H), THUMB_DARKNAVY)
    draw = ImageDraw.Draw(img)
    # Sunehra frame
    draw.rectangle([16, 16, W - 16, H - 16], outline=THUMB_GOLD, width=5)
    # Upar: gold pill me exam tag + uske neeche chapter_no gold me
    _thumb_pill(draw, W / 2, 52, _thumb_exam_tag(spec), 46,
                THUMB_GOLD, THUMB_DARKNAVY)
    chapter_no = str(spec.get("chapter_no", "")).strip()
    if chapter_no:
        _thumb_centered(draw, 150, chapter_no, 40, True, THUMB_GOLD)
    # Title — safed, bada, beech me (thumb.title; na ho to topic/yt_title se)
    title = str(spec.get("title") or spec.get("topic")
                or _topic_from_title(spec.get("yt_title", "")))
    size, lines = _thumb_fit_title(draw, title, 1120, 3, 122)
    lh = int(size * 1.28)
    total = lh * len(lines)
    y = 215 + (330 - total) // 2
    for line in lines:
        _thumb_centered(draw, y, line, size, True, (255, 255, 255))
        y += lh
    # Title ke neeche gold rekha
    draw.rounded_rectangle([W / 2 - 170, y + 14, W / 2 + 170, y + 26],
                           radius=6, fill=THUMB_GOLD)
    # Neeche: laal patti par channel naam
    draw.rectangle([0, H - 96, W, H], fill=RED)
    _thumb_centered(draw, H - 96 + (96 - 52) // 2 - 6, THUMB_BRAND, 52,
                    True, (255, 255, 255))
    img.save(out_path)
    return out_path


# ---------------------------------------------------------------------------
# Script validation (Guide 4.1, 6)
# ---------------------------------------------------------------------------
def load_and_validate_script(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        script = json.load(fh)
    title = script.get("yt_title", "")
    desc = script.get("yt_desc", "")
    if len(title) > 100:
        raise ValueError(f"yt_title {len(title)} akshar — 100 se kam rakho (invalidTitle).")
    if len(desc) > 5000:
        raise ValueError(f"yt_desc {len(desc)} akshar — 5000 ki seema hai.")
    scenes = script.get("scenes") or []
    if not scenes:
        raise ValueError("Script me scenes khaali hain.")
    for i, scene in enumerate(scenes, start=1):
        pts = scene.get("points", [])
        if len(pts) > MAX_POINTS:
            raise ValueError(
                f"Scene {i} me {len(pts)} points hain — adhiktam {MAX_POINTS}. "
                "Lambe points merge karo, tathya mat kaato."
            )
        if not scene.get("text"):
            raise ValueError(f"Scene {i} me narration 'text' khaali hai.")
        diagram = scene.get("diagram")
        if diagram is not None:
            boxes = diagram.get("boxes") or []
            if not diagram.get("title") or not boxes or len(boxes) > 5:
                raise ValueError(
                    f"Scene {i} ka diagram adhura hai — title aur 1-5 boxes chahiye."
                )
    return script


# ---------------------------------------------------------------------------
# Video assembly (ffmpeg)
# ---------------------------------------------------------------------------
def require_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg nahi mila! Video nahi ban sakta. Install karo: "
            "GitHub Actions me `sudo apt-get install -y ffmpeg`, "
            "local par apne OS ke package manager se."
        )
    return ffmpeg


def assemble_video(segments: list[tuple[str, str, float, float]],
                   out_path: str) -> str:
    """Segments: (slide_path, wav_path, wav_offset_sec, duration_sec) —
    ek scene ki kai slides usi scene ki awaaz ke alag-alag hisson par
    dikhti hain (text slides ke liye, Priya 22 Nov 2026)."""
    ffmpeg = require_ffmpeg()
    seg_files = []
    list_path = os.path.join(WORK_DIR, "concat.txt")
    for idx, (slide, wav, offset, dur) in enumerate(segments, start=1):
        seg = os.path.join(WORK_DIR, f"segment_{idx:03d}.mp4")
        subprocess.run(
            [ffmpeg, "-y", "-loop", "1", "-i", slide,
             "-ss", f"{offset:.2f}", "-i", wav,
             "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac",
             "-b:a", "192k", "-pix_fmt", "yuv420p", "-t", f"{dur:.2f}", seg],
            check=True, capture_output=True,
        )
        seg_files.append(seg)
    with open(list_path, "w", encoding="utf-8") as fh:
        for seg in seg_files:
            fh.write(f"file '{seg}'\n")
    subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
         "-c", "copy", out_path],
        check=True, capture_output=True,
    )
    return out_path


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------
def _next_pointer_after(current_path: str) -> str | None:
    """test_01.json -> test_02.json jaisa agla pointer, agar file maujud ho."""
    m = re.search(r"(\d+)(\.json)$", current_path)
    if not m:
        return None
    nxt = int(m.group(1)) + 1
    candidate = current_path[: m.start(1)] + f"{nxt:0{len(m.group(1))}d}" + m.group(2)
    full = candidate if os.path.isabs(candidate) else os.path.join(BASE_DIR, candidate)
    return candidate if os.path.exists(full) else None


async def _make_audio(narration_texts: list[str]) -> tuple[list[str], list[float]]:
    import live_tts

    audio_dir = os.path.join(WORK_DIR, "audio")
    pcm_paths = await live_tts.synthesize_scenes_sequential(narration_texts, audio_dir)
    wavs, durations = [], []
    for pcm in pcm_paths:
        wav = pcm.rsplit(".", 1)[0] + ".wav"
        live_tts.pcm_to_wav(pcm, wav)
        wavs.append(wav)
        durations.append(max(live_tts.pcm_duration_seconds(pcm), 1.0))
    return wavs, durations


def run(script_path: str, privacy: str = "private", upload: bool = True) -> str | None:
    script_abs = script_path if os.path.isabs(script_path) else os.path.join(BASE_DIR, script_path)
    script = load_and_validate_script(script_abs)
    os.makedirs(WORK_DIR, exist_ok=True)

    # 1) Slides — har scene: pehle head+points wali slide, phir usi scene ka
    # pura explanation LIKHA HUA text slides par (Priya, 22 Nov 2026:
    # student sunkar notes nahi likhega — sab likha bhi hona chahiye).
    # Neeche ki patti me sirf topic ka naam (Priya, 15 Nov 2026).
    topic = str(script.get("topic") or _topic_from_title(script.get("yt_title", "")))
    scene_parts = []  # har scene: {"paths": [intro, text1, ...], "chunks": [...]}
    for idx, scene in enumerate(script["scenes"], start=1):
        intro_path = os.path.join(WORK_DIR, f"slide_{idx:03d}.png")
        build_slide(scene, intro_path, topic=topic)
        paths = [intro_path]
        chunks = _split_text_chunks(scene["text"], max_words=90)
        for j, chunk in enumerate(chunks, start=1):
            text_path = os.path.join(WORK_DIR, f"slide_{idx:03d}_t{j}.png")
            build_text_slide(scene["head"], chunk, text_path, topic=topic)
            paths.append(text_path)
        scene_parts.append({"paths": paths, "chunks": chunks})
    total_slides = sum(len(p["paths"]) for p in scene_parts)
    print(f"Slides taiyaar: {total_slides} ({len(scene_parts)} scenes)")

    # 2) Awaaz — narration hamesha saaf kiya hua text (asal script text nahi)
    narration = [prepare_tts_text(scene["text"]) for scene in script["scenes"]]
    for idx, txt in enumerate(narration, start=1):
        print(f"Narration {idx}: {txt[:90]}...")
    wavs, durations = asyncio.run(_make_audio(narration))
    print(f"Audio taiyaar: {len(wavs)} scenes")

    # 3) ffmpeg se video — segments: intro slide ko scene ke samay ka
    # chhota hissa, baaki samay text slides me shabdon ke anupaat me
    segments = []
    for part, wav, dur in zip(scene_parts, wavs, durations):
        paths, chunks = part["paths"], part["chunks"]
        if len(paths) == 1 or dur <= 6:
            segments.append((paths[0], wav, 0.0, dur))
            continue
        intro_dur = min(max(dur * 0.18, 3.0), dur * 0.30)
        rest = dur - intro_dur
        weights = [max(len(c.split()), 1) for c in chunks]
        total_w = sum(weights)
        offset = 0.0
        segments.append((paths[0], wav, offset, intro_dur))
        offset += intro_dur
        for path, w in zip(paths[1:], weights):
            d = rest * w / total_w
            segments.append((path, wav, offset, d))
            offset += d
    video_path = os.path.join(WORK_DIR, "lesson_video.mp4")
    assemble_video(segments, video_path)
    print(f"Video taiyaar: {video_path}")

    if not upload:
        print("--no-upload: yahin ruk raha hoon, state update nahi hua.")
        return None

    # 4) Upload (default private)
    import youtube_upload

    # Thumbnail: script ka 'thumb' spec ho to runtime par banao (sabse pehle)
    # Design B: topic/yt_title bhi saath bhejo — title/tag ke fallback ke liye
    thumb_spec = script.get("thumb") or None
    if thumb_spec:
        gen_thumb = os.path.join(WORK_DIR, "thumbnail.png")
        spec = dict(thumb_spec)
        spec.setdefault("topic", topic)
        spec.setdefault("yt_title", script.get("yt_title", ""))
        _render_thumbnail(spec, gen_thumb)
        script = dict(script)
        script["thumb_file"] = os.path.relpath(gen_thumb, BASE_DIR)

    thumb = script.get("thumb_file") or None
    thumb_abs = None
    if thumb:
        candidate = os.path.join(BASE_DIR, thumb)
        thumb_abs = candidate if os.path.exists(candidate) else None
        if thumb_abs is None:
            print(f"WARNING: thumb_file mila nahi ({thumb}) — bina thumbnail upload hoga.")
    video_id = youtube_upload.upload_video(
        video_path,
        script["yt_title"],
        script.get("yt_desc", ""),
        privacy=privacy,
        thumbnail_file=thumb_abs,
        playlist_id=script.get("playlist_id") or None,
    )
    url = youtube_upload.video_url(video_id)
    print(f"Uploaded: {url}")
    print("Note: safalta ki pushti video page/API se verify karo — sirf is print par mat jao.")

    # 5) State files — ATOMIC update (state-loss trap se bachne ke liye)
    state = read_state()
    state["next"] = int(state.get("next", 1)) + 1
    atomic_write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    atomic_write(LATEST_FILE, url + "\n")
    nxt = _next_pointer_after(os.path.relpath(script_abs, BASE_DIR))
    if nxt:
        atomic_write(NEXT_FILE, nxt + "\n")
    else:
        print("WARNING: agli script ka pointer nahi mila — lesson_next.txt haath se sudharo.")
    return url


def main() -> None:
    parser = argparse.ArgumentParser(description="Lesson series engine")
    parser.add_argument(
        "--script", default=None,
        help="Script JSON ka path (default: lesson_next.txt wala pointer)",
    )
    parser.add_argument(
        "--privacy", default="private", choices=["private", "unlisted", "public"],
        help="Upload privacy (default: private)",
    )
    parser.add_argument("--no-upload", action="store_true",
                        help="Video banakar ruk jao, upload/state-update nahi")
    parser.add_argument("--count", type=int, default=1,
                        help="Ek run me kitne lesson lagatar upload karne hain (default: 1)")
    args = parser.parse_args()

    # --count loop (15 Nov 2026, Priya ka niyam: roz 4 video): har safal
    # upload ke baad pointer aage badhta hai; agli script na mile to loop
    # surakshit roke. Exception aaye to woh bahar jayegi — workflow ka
    # state-commit step (if: always()) pure hue lessons ka state bacha lega.
    total = max(1, args.count)
    for i in range(total):
        script_path = args.script or read_next_pointer()
        url = run(script_path, privacy=args.privacy, upload=not args.no_upload)
        if args.no_upload or args.script:
            break
        if url is None:
            print("Upload nahi hua — count loop yahin rok raha hoon.")
            break
        if i < total - 1:
            nxt = read_next_pointer()
            nxt_abs = nxt if os.path.isabs(nxt) else os.path.join(BASE_DIR, nxt)
            if not os.path.exists(nxt_abs):
                print(f"Agli script maujud nahi ({nxt}) — count loop samaapt.")
                break
            print(f"--- Agla lesson ({i + 2}/{total}): {nxt} ---")


if __name__ == "__main__":
    main()
