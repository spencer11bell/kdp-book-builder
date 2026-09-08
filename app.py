
import json
import os
import threading
import time
import uuid
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from reportlab.lib.pagesizes import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


# ============================================================
# CONFIG
# ============================================================

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

BUILDER_KEY = os.getenv("BUILDER_KEY", "change-me")
RUNWAYML_API_SECRET = os.getenv("RUNWAYML_API_SECRET", "")

RUNWAY_IMAGE_MODEL = os.getenv("RUNWAY_IMAGE_MODEL", "gen4_image")
RUNWAY_IMAGE_RATIO = os.getenv("RUNWAY_IMAGE_RATIO", "720:1280")

RUNWAY_API_BASE = "https://api.dev.runwayml.com/v1"
RUNWAY_API_VERSION = "2024-11-06"
MAX_RUNWAY_PROMPT_CHARS = 950

# TEST MODE:
# Leave this at 3 until the three interior pages + front/back covers are approved.
# FINAL PRODUCTION CHANGE: change 3 -> 80.
DEFAULT_PAGE_COUNT = 3

app = FastAPI(
    title="Cosmo Crew KDP Book Factory",
    version="4.0.0"
)

jobs: Dict[str, Dict[str, Any]] = {}


# ============================================================
# INPUT
# ============================================================

class JobPayload(BaseModel):
    mode: str = "TEST"
    publish: bool = False
    kind: str = "kids"

    brand: str = "Cosmo Crew"
    imprint: str = "Cosmo Crew Learning Adventures"

    series: str = "Space Adventure"
    world: str = "futuristic space adventure"

    topic: str = "Numbers 1-10"
    volume: int = 1
    age_band: str = "3-5"

    # IMPORTANT:
    # During testing this is 3.
    # When everything is approved, the only production change needed is 3 -> 80.
    page_count_target: int = DEFAULT_PAGE_COUNT

    trim_width: float = 8.5
    trim_height: float = 11

    language: str = "English"

    author_name: str = "Cosmo Crew Learning Adventures"
    subtitle: str = "Space Numbers Adventure"
    back_cover_blurb: str = (
        "Blast off with Nia, Mateo, and Anaya on a playful learning adventure "
        "through space. Kids can color, count, trace, match, and explore while "
        "building early-learning confidence one page at a time."
    )

    next_book: dict = {}
    back_cover_affirmations: list = []
    quality_policy: dict = {}
    diversity_policy: dict = {}
    interactive_learning: dict = {}
    page_plan: dict = {}

    builder_url: str = ""
    builder_key: str = ""


# ============================================================
# AUTH
# ============================================================

def authorize(key):
    if not key:
        raise HTTPException(status_code=401, detail="Missing X-Builder-Key")
    if key != BUILDER_KEY:
        raise HTTPException(status_code=401, detail="Invalid X-Builder-Key")


# ============================================================
# HELPERS
# ============================================================

def get_job_dir(job_id):
    folder = DATA_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def clean_prompt(text):
    cleaned = " ".join(str(text).split()).strip()
    if len(cleaned) <= MAX_RUNWAY_PROMPT_CHARS:
        return cleaned
    return cleaned[:MAX_RUNWAY_PROMPT_CHARS].rsplit(" ", 1)[0]


def runway_headers():
    return {
        "Authorization": f"Bearer {RUNWAYML_API_SECRET}",
        "Content-Type": "application/json",
        "X-Runway-Version": RUNWAY_API_VERSION,
    }


def http_json(method, url, payload=None, headers=None, timeout=120):
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url=url,
        data=body,
        headers=headers or {},
        method=method
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e}")


def download_file(url, destination):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        destination.write_bytes(response.read())


# ============================================================
# RUNWAY
# ============================================================

def create_runway_image(prompt):
    if not RUNWAYML_API_SECRET:
        raise RuntimeError("RUNWAYML_API_SECRET is missing.")

    safe_prompt = clean_prompt(prompt)

    response = http_json(
        "POST",
        f"{RUNWAY_API_BASE}/text_to_image",
        {
            "model": RUNWAY_IMAGE_MODEL,
            "ratio": RUNWAY_IMAGE_RATIO,
            "promptText": safe_prompt
        },
        runway_headers()
    )

    task_id = response.get("id")
    if not task_id:
        raise RuntimeError(f"No Runway task id: {response}")

    return task_id


def wait_for_runway_image(task_id, timeout_seconds=420):
    started = time.time()

    while time.time() - started < timeout_seconds:
        result = http_json(
            "GET",
            f"{RUNWAY_API_BASE}/tasks/{task_id}",
            headers=runway_headers()
        )

        status = str(result.get("status", "")).upper()

        if status == "SUCCEEDED":
            output = result.get("output") or []
            if not output:
                raise RuntimeError("Runway returned no image.")
            return output[0]

        if status in {"FAILED", "CANCELED", "CANCELLED"}:
            raise RuntimeError(f"Runway failed: {result}")

        time.sleep(5)

    raise RuntimeError(f"Runway timed out: {task_id}")


def generate_image(prompt, output_path):
    safe_prompt = clean_prompt(prompt)
    task_id = create_runway_image(safe_prompt)
    image_url = wait_for_runway_image(task_id)
    download_file(image_url, output_path)

    return {
        "task_id": task_id,
        "prompt_length": len(safe_prompt),
        "image_url": image_url,
        "local_file": str(output_path)
    }


# ============================================================
# CHARACTER + STYLE RULES
# ============================================================

NIA = (
    "Nia, a cheerful young Black girl astronaut with two natural puff ponytails"
)

MATEO = (
    "Mateo, a cheerful young Latino boy astronaut with short dark hair"
)

ANAYA = (
    "Anaya, a cheerful young South Asian girl astronaut with long dark hair"
)

# THIS IS INTENTIONALLY STRICT.
# Skin is NEVER automatically filled, shaded, gray, or black.
# Ethnicity is communicated through hair/facial design only.
INTERIOR_STYLE = """
Premium black-and-white children's coloring-book line art.
PURE BLACK OUTLINES on a PURE WHITE background only.
NO color. NO gray. NO shading. NO gradients. NO halftones. NO filled shadows.
ALL HUMAN SKIN AREAS MUST REMAIN PURE WHITE AND COMPLETELY UNFILLED,
including faces, necks, arms, hands, legs, and any visible skin.
DO NOT use black fill, gray fill, hatching, crosshatching, stippling, or tone
to represent skin color or ethnicity. Represent ethnicity ONLY through
hair texture, hairstyles, facial features, and character design.
Hair may use clean outline detail but should still contain open white coloring space.
Clothing and astronaut suits must remain white inside the outlines for coloring.
Large open coloring areas, bold smooth professional outlines, child-friendly detail,
no text, no letters, no numbers, no watermark, no signature.
Commercial KDP coloring-book quality for ages 3-5.
"""

FRONT_COVER_STYLE = """
Premium full-color children's book cover illustration, polished commercial KDP quality.
Bright cinematic outer-space adventure, joyful and inviting, modern dimensional children's art.
Show Nia, Mateo, and Anaya together as the heroic Cosmo Crew in a beautiful futuristic space scene.
Keep the TOP 28 PERCENT of the composition visually clean and relatively uncluttered for title text.
Keep the BOTTOM 12 PERCENT visually clean for subtitle/series text.
No generated words, no letters, no logos, no watermark, no fake title text.
Portrait composition, strong focal point, rich but tasteful colors, premium bookstore-ready cover.
"""

BACK_COVER_STYLE = """
Premium full-color children's book BACK COVER illustration matching the front cover.
Beautiful futuristic outer-space environment with friendly planets, stars, rocket elements,
and subtle appearances of Nia, Mateo, and Anaya around the edges.
Keep the CENTER-LEFT and UPPER-MIDDLE areas calm and uncluttered for readable blurb text.
Keep the BOTTOM-RIGHT corner clean and light for a retail barcode area.
No generated words, no letters, no logos, no watermark, no fake text.
Portrait composition, polished commercial KDP cover quality.
"""


# ============================================================
# 80-PAGE MASTER PLAN
# ============================================================

def _page(title, instruction, prompt):
    return {
        "title": title,
        "instruction": instruction,
        "prompt": prompt,
    }


def build_master_page_plan(payload) -> List[Dict[str, str]]:
    world = payload.get("world", "futuristic space adventure")

    plans = [
        _page(
            "COUNT THE COSMIC FRIENDS",
            "Count the friendly space friends.",
            f"{NIA}, {MATEO}, and {ANAYA} stand together on a moon base with exactly THREE cute friendly alien pets. Large simple shapes, clear separation, easy counting, {world}."
        ),
        _page(
            "TRACE THE ROCKET PATH",
            "Follow the path to the rocket.",
            f"{MATEO} stands at one side of a moon valley and a friendly rocket waits at the other side. Create one wide, simple winding path between them with a few large moon rocks and stars. Easy preschool tracing activity, {world}."
        ),
        _page(
            "FIND THE MATCH",
            "Find the two planets that match.",
            f"{ANAYA} studies FOUR large friendly planets in a simple space observatory. Exactly TWO planets share the same obvious ring-and-crater pattern and the other two are clearly different. Large open shapes, easy preschool matching activity, {world}."
        ),
    ]

    count_objects = [
        ("star flowers", NIA), ("planets", MATEO), ("friendly aliens", ANAYA),
        ("space crystals", NIA), ("rocket ships", MATEO), ("moon rocks", ANAYA),
        ("cosmic gems", NIA), ("space bubbles", MATEO), ("tiny moons", ANAYA),
        ("shooting stars", NIA), ("robot helpers", MATEO), ("satellites", ANAYA),
        ("comets", NIA), ("astronaut flags", MATEO), ("space cupcakes", ANAYA),
        ("alien pets", NIA), ("moon flowers", MATEO), ("rocket windows", ANAYA),
        ("planet rings", NIA), ("space helmets", MATEO),
    ]

    for idx, (obj, character) in enumerate(count_objects, start=1):
        n = (idx % 10) + 1
        plans.append(
            _page(
                f"COUNT THE {obj.upper()}",
                f"Find and count {n} {obj}.",
                f"{character} explores a friendly space scene containing EXACTLY {n} large {obj}. Every object is clearly separated, obvious, and easy for a preschool child to count. Simple premium composition, {world}."
            )
        )

    activities = [
        ("BIG OR SMALL?", "Find the biggest rocket.", f"{NIA} stands beside THREE rockets: clearly small, medium, and very large."),
        ("SAME OR DIFFERENT?", "Find the different alien.", f"{MATEO} meets FOUR cute aliens. Three match and one is obviously different."),
        ("SPACE SHAPES", "Find circles, squares, and triangles.", f"{ANAYA} explores a simple control room with large circle, square, and triangle shaped objects."),
        ("WHAT COMES NEXT?", "Finish the simple pattern.", "Large simple visual pattern: rocket, planet, rocket, planet, rocket, blank final position."),
        ("MOON MAZE", "Help Nia reach the rover.", f"{NIA} stands on one side of a very simple preschool maze and a moon rover waits on the other."),
        ("ROCKET MAZE", "Help Mateo reach the rocket.", f"{MATEO} stands on one side of a very simple preschool maze and a rocket waits on the other."),
        ("ALIEN MAZE", "Help Anaya reach the friendly alien.", f"{ANAYA} stands on one side of a very simple preschool maze and one cute alien waits on the other."),
        ("FIND THE PAIR", "Which two rockets match?", "FOUR large rockets, exactly TWO with the same obvious window and fin design."),
        ("FIND THE PAIR", "Which two aliens match?", "FOUR large friendly aliens, exactly TWO with the same obvious antenna and ear design."),
        ("FIND THE PAIR", "Which two moons match?", "FOUR large moons, exactly TWO with the same obvious crater pattern."),
        ("TALL OR SHORT?", "Find the tallest robot.", f"{NIA} stands beside THREE friendly robots of clearly different heights."),
        ("NEAR OR FAR?", "Which planet is closest?", f"{MATEO} looks through a large window at THREE planets positioned clearly near, middle, and far."),
        ("MORE OR LESS?", "Which group has more stars?", f"{ANAYA} compares TWO simple groups of large stars, one group clearly containing more."),
        ("LEFT OR RIGHT?", "Find the rocket on the left.", "Two very large rockets, one clearly on the left and one clearly on the right."),
        ("ABOVE OR BELOW?", "Find the planet above the rover.", f"{NIA} stands beside a rover with one planet clearly above and one clearly below."),
        ("INSIDE OR OUTSIDE?", "Find the alien inside the rocket.", "One cute alien clearly inside a large open rocket doorway and one alien clearly outside."),
        ("OPEN OR CLOSED?", "Find the open treasure chest.", f"{MATEO} stands beside TWO cosmic treasure chests, one clearly open and one clearly closed."),
        ("DAY OR NIGHT?", "Find the sleepy moon scene.", f"{ANAYA} compares TWO simple space windows: one bright starry scene and one calm sleepy moon scene."),
        ("FIND THE SHADOW SHAPE", "Match the rocket to its outline.", "One large rocket and THREE simple outline silhouettes, only one matching the rocket shape."),
        ("FINISH THE ROCKET", "Draw the missing window.", f"{NIA} stands beside a large simple rocket with one obvious empty circular window position."),
        ("FINISH THE ALIEN", "Draw the missing antenna.", f"{MATEO} meets a cute alien with one obvious missing antenna."),
        ("FINISH THE ROBOT", "Draw the missing arm.", f"{ANAYA} stands beside a friendly robot with one obvious missing arm."),
        ("SPACE TREASURE HUNT", "Find the crystal, rocket toy, and moon gem.", f"{NIA} searches a simple uncluttered moon landscape with THREE hidden-but-visible large objects."),
        ("SPACE TREASURE HUNT", "Find the helmet, star, and robot.", f"{MATEO} searches a simple space station room with THREE hidden-but-visible large objects."),
        ("SPACE TREASURE HUNT", "Find the flag, planet toy, and alien pet.", f"{ANAYA} searches a simple moon park with THREE hidden-but-visible large objects."),
        ("MATCH THE HELMETS", "Find the two matching helmets.", "FOUR large astronaut helmets, exactly TWO with the same obvious visor and star emblem shape."),
        ("MATCH THE ROVERS", "Find the two matching rovers.", "FOUR large moon rovers, exactly TWO with the same obvious wheel and antenna design."),
        ("MATCH THE STARS", "Find the two matching stars.", "FOUR large whimsical stars, exactly TWO with the same obvious face and point pattern."),
        ("COUNT THE WINDOWS", "How many windows are on the rocket?", f"{NIA} stands beside one large rocket with exactly FIVE large round windows."),
        ("COUNT THE BUTTONS", "How many big buttons can you find?", f"{MATEO} stands at one simple control panel with exactly SIX large circular buttons."),
        ("COUNT THE FLAGS", "How many space flags can you find?", f"{ANAYA} explores a moon base with exactly FOUR large flags."),
        ("COLOR THE GIANT PLANET", "Make the biggest planet your favorite colors.", f"{NIA} stands beside one giant ringed planet filling much of the background, with few simple stars."),
        ("COLOR THE ROCKET", "Color the Cosmo Crew rocket.", f"{MATEO} stands proudly beside one large friendly futuristic rocket, simple open coloring areas."),
        ("COLOR THE MOON BASE", "Color the Cosmo Crew moon base.", f"{ANAYA} stands outside one friendly futuristic moon base with large simple architecture."),
        ("COLOR THE ALIEN PET", "Color the friendly cosmic pet.", f"{NIA} kneels beside one large adorable alien pet with simple expressive features."),
        ("COLOR THE SPACE GARDEN", "Color the moon flowers.", f"{MATEO} explores a simple moon garden with several oversized open-petal cosmic flowers."),
        ("COLOR THE ROBOT", "Color the friendly helper robot.", f"{ANAYA} stands beside one large friendly round-bodied robot with open coloring areas."),
        ("TRACE THE MOON ROAD", "Trace the road to the base.", f"{NIA} stands beside one very wide dotted-style path represented as clean open guide lines leading to a moon base. No text or numbers."),
        ("TRACE THE STAR TRAIL", "Follow the star trail.", f"{MATEO} follows one simple curved trail of large star shapes toward a rocket."),
        ("TRACE THE PLANET PATH", "Follow the path around the planets.", f"{ANAYA} follows one simple looping path around THREE large planets."),
        ("SPOT THE DIFFERENCE", "Find the rocket that is different.", "THREE large simple rockets. Two match exactly and one has one obvious different fin."),
        ("SPOT THE DIFFERENCE", "Find the robot that is different.", "THREE large friendly robots. Two match exactly and one has one obvious different antenna."),
        ("SPOT THE DIFFERENCE", "Find the planet that is different.", "THREE large planets. Two match exactly and one has one obvious different ring."),
        ("SORT THE SHAPES", "Find all the circles.", f"{NIA} stands in a control room with a small number of very large circles, squares, and triangles."),
        ("SORT THE SHAPES", "Find all the triangles.", f"{MATEO} stands in a space workshop with a small number of very large circles, squares, and triangles."),
        ("SORT THE SHAPES", "Find all the squares.", f"{ANAYA} stands in a moon lab with a small number of very large circles, squares, and triangles."),
        ("WHICH ONE FLIES?", "Find the rocket.", f"{NIA} looks at THREE large objects: one rocket, one rover, and one moon base."),
        ("WHICH ONE ROLLS?", "Find the rover.", f"{MATEO} looks at THREE large objects: one rover, one rocket, and one telescope."),
        ("WHICH ONE LOOKS AT STARS?", "Find the telescope.", f"{ANAYA} looks at THREE large objects: one telescope, one helmet, and one rover."),
        ("COSMO CREW PICNIC", "Color the crew's moon picnic.", f"{NIA}, {MATEO}, and {ANAYA} enjoy a simple friendly picnic on the moon with a few large snacks and one alien pet."),
        ("COSMO CREW PLAYTIME", "Color the crew playing together.", f"{NIA}, {MATEO}, and {ANAYA} play with one large floating space ball inside a simple zero-gravity room."),
        ("COSMO CREW GARDEN", "Color the crew helping the moon garden.", f"{NIA}, {MATEO}, and {ANAYA} water oversized moon flowers in a simple garden."),
        ("COSMO CREW BUILDERS", "Color the crew building a rover.", f"{NIA}, {MATEO}, and {ANAYA} work together beside one simple half-built moon rover with a few large parts."),
        ("COSMO CREW RESCUE", "Color the friendly rescue scene.", f"{NIA}, {MATEO}, and {ANAYA} help one cute alien pet beside a small moon rock, calm happy scene."),
        ("COSMO CREW DISCOVERY", "Color the new planet discovery.", f"{NIA}, {MATEO}, and {ANAYA} stand at a window looking at one giant beautiful ringed planet."),
        ("MY FAVORITE ROCKET", "Color the rocket your own way.", "One large centered friendly futuristic rocket, very clean white background with a few stars."),
        ("MY FAVORITE PLANET", "Color the planet your own way.", "One giant centered whimsical ringed planet with large open continents and rings, few stars."),
        ("MY FAVORITE ALIEN", "Color the alien your own way.", "One large centered friendly alien character with simple open body shapes, no human character."),
        ("MY FAVORITE ROBOT", "Color the robot your own way.", "One large centered friendly robot with simple open body panels and round shapes."),
        ("MY SPACE MASTERPIECE", "Use your imagination.", f"{NIA}, {MATEO}, and {ANAYA} explore a simple magical alien world with one rocket, one planet, and one friendly creature."),
        ("COSMO CREW CELEBRATION", "Color the celebration.", f"{NIA}, {MATEO}, and {ANAYA} celebrate together inside a friendly space station with a few large stars and balloons shaped like planets."),
        ("YOU DID IT!", "Color the crew's proud moment.", f"{NIA}, {MATEO}, and {ANAYA} proudly hold one large BLANK achievement plaque with no writing inside it."),
        ("REACH FOR THE STARS", "Keep learning and exploring.", f"{NIA}, {MATEO}, and {ANAYA} stand on a peaceful moon hill beneath one giant ringed planet and a simple starry sky."),
        ("NEXT ADVENTURE", "The Cosmo Crew is ready for more.", f"{NIA}, {MATEO}, and {ANAYA} walk toward one glowing futuristic portal with their rocket nearby. No text."),
    ]

    # Guarantee exactly 80 available plans by adding clean premium variations if needed.
    variation_index = 1
    while len(plans) < 80:
        character = [NIA, MATEO, ANAYA][(variation_index - 1) % 3]
        plans.append(
            _page(
                f"SPACE EXPLORER {variation_index}",
                "Color the space adventure.",
                f"{character} explores a unique simple child-friendly cosmic scene with one large focal object, a few stars, and generous open coloring areas. Variation {variation_index}. {world}."
            )
        )
        variation_index += 1

    return plans[:80]


def build_page_plan(payload):
    requested = int(payload.get("page_count_target", DEFAULT_PAGE_COUNT))

    if requested < 1:
        requested = 1
    if requested > 80:
        requested = 80

    return build_master_page_plan(payload)[:requested]


# ============================================================
# PDF INTERIOR
# ============================================================

def draw_title(c, width, height, title, instruction):
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(width / 2, height - 0.48 * inch, title)

    c.setFont("Helvetica", 10.5)
    c.drawCentredString(width / 2, height - 0.75 * inch, instruction)


def place_image(c, image_path, width, height):
    image = ImageReader(str(image_path))
    sw, sh = image.getSize()

    available_width = width - 0.55 * inch
    available_height = height - 1.20 * inch

    scale = min(
        available_width / sw,
        available_height / sh
    )

    dw = sw * scale
    dh = sh * scale

    x = (width - dw) / 2
    y = 0.28 * inch

    c.drawImage(
        image,
        x,
        y,
        width=dw,
        height=dh,
        preserveAspectRatio=True,
        mask="auto"
    )


def create_interior_pdf(path, payload, page_plan, images):
    width = float(payload.get("trim_width", 8.5)) * inch
    height = float(payload.get("trim_height", 11)) * inch

    c = canvas.Canvas(
        str(path),
        pagesize=(width, height)
    )

    for page, image_path in zip(page_plan, images):
        draw_title(
            c,
            width,
            height,
            page["title"],
            page["instruction"]
        )
        place_image(
            c,
            image_path,
            width,
            height
        )
        c.showPage()

    c.save()


# ============================================================
# COVER PREVIEWS
# ============================================================

def _cover_page_size(payload):
    width = float(payload.get("trim_width", 8.5)) * inch
    height = float(payload.get("trim_height", 11)) * inch
    return width, height


def _draw_cover_art_full_bleed(c, art_path, width, height):
    image = ImageReader(str(art_path))
    sw, sh = image.getSize()

    scale = max(width / sw, height / sh)
    dw = sw * scale
    dh = sh * scale

    x = (width - dw) / 2
    y = (height - dh) / 2

    c.drawImage(
        image,
        x,
        y,
        width=dw,
        height=dh,
        preserveAspectRatio=True,
        mask="auto"
    )


def create_front_cover_preview(path, payload, art_path):
    width, height = _cover_page_size(payload)
    c = canvas.Canvas(str(path), pagesize=(width, height))

    _draw_cover_art_full_bleed(c, art_path, width, height)

    # Clean translucent effect is not used; instead use simple readable white text.
    title = str(payload.get("brand", "Cosmo Crew"))
    subtitle = str(payload.get("subtitle", "Space Numbers Adventure"))
    age_band = str(payload.get("age_band", "3-5"))

    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 30)
    c.drawCentredString(width / 2, height - 0.72 * inch, title)

    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(width / 2, height - 1.10 * inch, subtitle)

    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(
        width / 2,
        0.42 * inch,
        f"Ages {age_band}  |  Coloring + Early Learning Adventure"
    )

    c.showPage()
    c.save()


def _wrap_text(c, text, x, y, max_width, font_name, font_size, leading):
    words = text.split()
    line = ""
    current_y = y

    for word in words:
        test = word if not line else f"{line} {word}"
        if c.stringWidth(test, font_name, font_size) <= max_width:
            line = test
        else:
            c.drawString(x, current_y, line)
            current_y -= leading
            line = word

    if line:
        c.drawString(x, current_y, line)

    return current_y


def create_back_cover_preview(path, payload, art_path):
    width, height = _cover_page_size(payload)
    c = canvas.Canvas(str(path), pagesize=(width, height))

    _draw_cover_art_full_bleed(c, art_path, width, height)

    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(0.65 * inch, height - 0.90 * inch, "Join the Cosmo Crew!")

    blurb = str(payload.get("back_cover_blurb", "")).strip()
    c.setFont("Helvetica-Bold", 11.5)
    _wrap_text(
        c,
        blurb,
        0.65 * inch,
        height - 1.35 * inch,
        width - 1.60 * inch,
        "Helvetica-Bold",
        11.5,
        16
    )

    feature_y = height - 3.25 * inch
    c.setFont("Helvetica-Bold", 12)
    for feature in [
        "Coloring + counting",
        "Simple tracing + matching",
        "Preschool-friendly activities",
        "Large open coloring spaces",
        "Positive learning adventure",
    ]:
        c.drawString(0.80 * inch, feature_y, f"• {feature}")
        feature_y -= 0.26 * inch

    c.setFont("Helvetica-Bold", 10)
    c.drawString(
        0.65 * inch,
        0.62 * inch,
        str(payload.get("author_name", "Cosmo Crew Learning Adventures"))
    )

    # Barcode safe-zone preview.
    barcode_w = 2.00 * inch
    barcode_h = 1.20 * inch
    barcode_x = width - barcode_w - 0.35 * inch
    barcode_y = 0.30 * inch

    c.setFillColorRGB(1, 1, 1)
    c.rect(barcode_x, barcode_y, barcode_w, barcode_h, fill=1, stroke=0)

    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 8)
    c.drawCentredString(
        barcode_x + barcode_w / 2,
        barcode_y + barcode_h / 2,
        "BARCODE AREA"
    )

    c.showPage()
    c.save()


def create_cover_preview_bundle(path, payload, front_art, back_art):
    width, height = _cover_page_size(payload)
    c = canvas.Canvas(str(path), pagesize=(width, height))

    # Front page
    _draw_cover_art_full_bleed(c, front_art, width, height)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 30)
    c.drawCentredString(
        width / 2,
        height - 0.72 * inch,
        str(payload.get("brand", "Cosmo Crew"))
    )
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(
        width / 2,
        height - 1.10 * inch,
        str(payload.get("subtitle", "Space Numbers Adventure"))
    )
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(
        width / 2,
        0.42 * inch,
        f"Ages {payload.get('age_band', '3-5')}  |  Coloring + Early Learning Adventure"
    )
    c.showPage()

    # Back page
    _draw_cover_art_full_bleed(c, back_art, width, height)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(0.65 * inch, height - 0.90 * inch, "Join the Cosmo Crew!")
    c.setFont("Helvetica-Bold", 11.5)
    _wrap_text(
        c,
        str(payload.get("back_cover_blurb", "")),
        0.65 * inch,
        height - 1.35 * inch,
        width - 1.60 * inch,
        "Helvetica-Bold",
        11.5,
        16
    )

    feature_y = height - 3.25 * inch
    c.setFont("Helvetica-Bold", 12)
    for feature in [
        "Coloring + counting",
        "Simple tracing + matching",
        "Preschool-friendly activities",
        "Large open coloring spaces",
        "Positive learning adventure",
    ]:
        c.drawString(0.80 * inch, feature_y, f"• {feature}")
        feature_y -= 0.26 * inch

    barcode_w = 2.00 * inch
    barcode_h = 1.20 * inch
    barcode_x = width - barcode_w - 0.35 * inch
    barcode_y = 0.30 * inch

    c.setFillColorRGB(1, 1, 1)
    c.rect(barcode_x, barcode_y, barcode_w, barcode_h, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 8)
    c.drawCentredString(
        barcode_x + barcode_w / 2,
        barcode_y + barcode_h / 2,
        "BARCODE AREA"
    )

    c.showPage()
    c.save()


# ============================================================
# METADATA
# ============================================================

def create_metadata(path, payload, job_id, page_plan, results, cover_results):
    metadata = {
        "job_id": job_id,
        "brand": payload.get("brand"),
        "imprint": payload.get("imprint"),
        "series": payload.get("series"),
        "topic": payload.get("topic"),
        "volume": payload.get("volume"),
        "age_band": payload.get("age_band"),
        "page_count": len(page_plan),
        "publish_enabled": False,
        "runway_model": RUNWAY_IMAGE_MODEL,
        "runway_ratio": RUNWAY_IMAGE_RATIO,
        "pages": page_plan,
        "runway_tasks": results,
        "cover_tasks": cover_results,
        "next_book": "Cosmo Crew: Alphabet Adventure",
        "production_status": (
            "3-PAGE + FRONT/BACK COVER TEST"
            if len(page_plan) == 3
            else f"{len(page_plan)}-PAGE PRODUCTION BUILD"
        ),
        "seasonal_content": False,
        "skin_fill_policy": (
            "All human skin areas must remain pure white and unfilled in interior line art."
        ),
    }

    path.write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8"
    )


# ============================================================
# BUILD JOB
# ============================================================

def build_job(job_id, payload, base_url):
    try:
        jobs[job_id]["status"] = "RUNNING"

        folder = get_job_dir(job_id)
        page_plan = build_page_plan(payload)

        images = []
        results = []
        cover_results = []

        total = len(page_plan)

        # ----------------------------------------------------
        # 1) TEST INTERIOR PAGES
        # ----------------------------------------------------
        for index, page in enumerate(page_plan, start=1):
            jobs[job_id]["progress"] = (
                f"Generating test page {index} of {total}: {page['title']}"
            )

            image_path = folder / f"page_{index:02d}.png"

            full_prompt = (
                INTERIOR_STYLE
                + "\n"
                + page["prompt"]
            )

            result = generate_image(
                full_prompt,
                image_path
            )

            images.append(image_path)
            results.append(result)

            jobs[job_id]["pages_completed"] = index
            jobs[job_id]["pages_total"] = total

        # ----------------------------------------------------
        # 2) FRONT COVER ART
        # ----------------------------------------------------
        jobs[job_id]["progress"] = "Generating front cover art"

        front_cover_art = folder / "front_cover_art.png"
        front_cover_result = generate_image(
            FRONT_COVER_STYLE,
            front_cover_art
        )
        cover_results.append({
            "type": "front_cover",
            **front_cover_result
        })

        # ----------------------------------------------------
        # 3) BACK COVER ART
        # ----------------------------------------------------
        jobs[job_id]["progress"] = "Generating back cover art"

        back_cover_art = folder / "back_cover_art.png"
        back_cover_result = generate_image(
            BACK_COVER_STYLE,
            back_cover_art
        )
        cover_results.append({
            "type": "back_cover",
            **back_cover_result
        })

        # ----------------------------------------------------
        # 4) BUILD PREVIEW FILES
        # ----------------------------------------------------
        jobs[job_id]["progress"] = "Building test interior and cover previews"

        interior_pdf = folder / "cosmo_crew_test_interior.pdf"
        front_cover_pdf = folder / "front_cover_preview.pdf"
        back_cover_pdf = folder / "back_cover_preview.pdf"
        cover_bundle_pdf = folder / "front_back_cover_preview.pdf"
        metadata_file = folder / "metadata.json"
        package_file = folder / "cosmo_crew_test_package.zip"

        create_interior_pdf(
            interior_pdf,
            payload,
            page_plan,
            images
        )

        create_front_cover_preview(
            front_cover_pdf,
            payload,
            front_cover_art
        )

        create_back_cover_preview(
            back_cover_pdf,
            payload,
            back_cover_art
        )

        create_cover_preview_bundle(
            cover_bundle_pdf,
            payload,
            front_cover_art,
            back_cover_art
        )

        create_metadata(
            metadata_file,
            payload,
            job_id,
            page_plan,
            results,
            cover_results
        )

        jobs[job_id]["progress"] = "Packaging test files"

        with zipfile.ZipFile(
            package_file,
            "w",
            zipfile.ZIP_DEFLATED
        ) as archive:
            archive.write(interior_pdf, interior_pdf.name)
            archive.write(front_cover_pdf, front_cover_pdf.name)
            archive.write(back_cover_pdf, back_cover_pdf.name)
            archive.write(cover_bundle_pdf, cover_bundle_pdf.name)
            archive.write(front_cover_art, front_cover_art.name)
            archive.write(back_cover_art, back_cover_art.name)
            archive.write(metadata_file, metadata_file.name)

            for image in images:
                archive.write(image, image.name)

        jobs[job_id].update(
            {
                "status": "SUCCEEDED",
                "progress": (
                    f"{total}-page test + front/back covers ready"
                ),
                "publish_enabled": False,
                "page_count": total,
                "pages_completed": total,
                "pages_total": total,
                "interior_pdf_url":
                    f"{base_url}/files/{job_id}/{interior_pdf.name}",
                "front_cover_art_url":
                    f"{base_url}/files/{job_id}/{front_cover_art.name}",
                "back_cover_art_url":
                    f"{base_url}/files/{job_id}/{back_cover_art.name}",
                "front_cover_preview_url":
                    f"{base_url}/files/{job_id}/{front_cover_pdf.name}",
                "back_cover_preview_url":
                    f"{base_url}/files/{job_id}/{back_cover_pdf.name}",
                "cover_preview_url":
                    f"{base_url}/files/{job_id}/{cover_bundle_pdf.name}",
                "metadata_url":
                    f"{base_url}/files/{job_id}/{metadata_file.name}",
                "package_url":
                    f"{base_url}/files/{job_id}/{package_file.name}",
            }
        )

    except Exception as e:
        jobs[job_id].update(
            {
                "status": "FAILED",
                "progress": "Book generation failed",
                "error": repr(e)
            }
        )


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "service": "Cosmo Crew KDP Book Factory",
        "version": "4.0.0",
        "status": "ready",
        "runway_enabled": bool(RUNWAYML_API_SECRET),
        "runway_model": RUNWAY_IMAGE_MODEL,
        "test_pages": DEFAULT_PAGE_COUNT,
        "production_ready_for": 80,
        "covers_enabled": True,
        "publish_enabled": False,
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "version": "4.0.0",
        "runway_enabled": bool(RUNWAYML_API_SECRET),
        "test_pages": DEFAULT_PAGE_COUNT,
        "covers_enabled": True,
    }


@app.post("/jobs")
def create_job(
    payload: JobPayload,
    request: Request,
    x_builder_key: str | None = Header(default=None)
):
    authorize(x_builder_key)

    if payload.publish:
        raise HTTPException(
            status_code=400,
            detail=(
                "Amazon publishing is disabled during preview testing."
            )
        )

    job_id = uuid.uuid4().hex[:12]
    base_url = str(request.base_url).rstrip("/")

    requested_pages = int(payload.page_count_target)
    if requested_pages < 1:
        requested_pages = 1
    if requested_pages > 80:
        requested_pages = 80

    jobs[job_id] = {
        "job_id": job_id,
        "status": "QUEUED",
        "progress": (
            f"Preparing {requested_pages}-page test + front/back covers"
        ),
        "pages_completed": 0,
        "pages_total": requested_pages,
        "created_at": time.time(),
        "status_url": f"{base_url}/status/{job_id}",
        "publish_enabled": False,
    }

    thread = threading.Thread(
        target=build_job,
        args=(
            job_id,
            payload.model_dump(),
            base_url
        ),
        daemon=True
    )

    thread.start()

    return jobs[job_id]


@app.get("/status/{job_id}")
def get_status(
    job_id: str,
    x_builder_key: str | None = Header(default=None)
):
    authorize(x_builder_key)

    if job_id not in jobs:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    return jobs[job_id]


@app.get("/files/{job_id}/{filename}")
def get_file(job_id, filename):
    file_path = (
        DATA_DIR
        / job_id
        / filename
    ).resolve()

    if not str(file_path).startswith(str(DATA_DIR.resolve())):
        raise HTTPException(
            status_code=403,
            detail="Invalid path"
        )

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail="File not found"
        )

    return FileResponse(
        str(file_path),
        filename=filename
    )
