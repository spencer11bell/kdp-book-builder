import json
import os
import threading
import time
import uuid
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import Any, Dict

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

RUNWAY_IMAGE_MODEL = os.getenv(
    "RUNWAY_IMAGE_MODEL",
    "gen4_image"
)

RUNWAY_IMAGE_RATIO = os.getenv(
    "RUNWAY_IMAGE_RATIO",
    "720:1280"
)

RUNWAY_API_BASE = "https://api.dev.runwayml.com/v1"
RUNWAY_API_VERSION = "2024-11-06"
MAX_RUNWAY_PROMPT_CHARS = 950

app = FastAPI(
    title="Cosmo Crew KDP Book Factory",
    version="3.0.0"
)

jobs: Dict[str, Dict[str, Any]] = {}


# ============================================================
# INPUT
# ============================================================

class JobPayload(BaseModel):

    mode: str = "FULL_PREVIEW"
    publish: bool = False
    kind: str = "kids"

    brand: str = "Cosmo Crew"
    imprint: str = "Cosmo Crew Learning Adventures"

    series: str = "Space Adventure"
    world: str = "futuristic space adventure"

    topic: str = "Numbers 1-10"
    volume: int = 1
    age_band: str = "3-5"

    page_count_min: int = 25
    page_count_target: int = 28
    page_count_max: int = 30

    double_sided: bool = True
    two_sided_art: bool = True

    trim_width: float = 8.5
    trim_height: float = 11

    language: str = "English"

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
        raise HTTPException(
            status_code=401,
            detail="Missing X-Builder-Key"
        )

    if key != BUILDER_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid X-Builder-Key"
        )


# ============================================================
# HELPERS
# ============================================================

def get_job_dir(job_id):

    folder = DATA_DIR / job_id

    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    return folder


def clean_prompt(text):

    cleaned = " ".join(
        str(text).split()
    ).strip()

    if len(cleaned) <= MAX_RUNWAY_PROMPT_CHARS:
        return cleaned

    return cleaned[
        :MAX_RUNWAY_PROMPT_CHARS
    ].rsplit(
        " ",
        1
    )[0]


def runway_headers():

    return {
        "Authorization":
            f"Bearer {RUNWAYML_API_SECRET}",

        "Content-Type":
            "application/json",

        "X-Runway-Version":
            RUNWAY_API_VERSION,
    }


def http_json(
    method,
    url,
    payload=None,
    headers=None,
    timeout=120
):

    body = None

    if payload is not None:
        body = json.dumps(
            payload
        ).encode("utf-8")

    req = urllib.request.Request(
        url=url,
        data=body,
        headers=headers or {},
        method=method
    )

    try:

        with urllib.request.urlopen(
            req,
            timeout=timeout
        ) as response:

            raw = response.read().decode(
                "utf-8"
            )

            return (
                json.loads(raw)
                if raw
                else {}
            )

    except urllib.error.HTTPError as e:

        error_body = e.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"HTTP {e.code}: {error_body}"
        )

    except urllib.error.URLError as e:

        raise RuntimeError(
            f"Network error: {e}"
        )


def download_file(url, destination):

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urllib.request.urlopen(
        req,
        timeout=180
    ) as response:

        destination.write_bytes(
            response.read()
        )


# ============================================================
# RUNWAY
# ============================================================

def create_runway_image(prompt):

    if not RUNWAYML_API_SECRET:

        raise RuntimeError(
            "RUNWAYML_API_SECRET is missing."
        )

    prompt = clean_prompt(prompt)

    payload = {
        "model": RUNWAY_IMAGE_MODEL,
        "ratio": RUNWAY_IMAGE_RATIO,
        "promptText": prompt
    }

    response = http_json(
        "POST",
        f"{RUNWAY_API_BASE}/text_to_image",
        payload,
        runway_headers()
    )

    task_id = response.get("id")

    if not task_id:

        raise RuntimeError(
            f"No Runway task id: {response}"
        )

    return task_id


def wait_for_runway_image(
    task_id,
    timeout_seconds=420
):

    started = time.time()

    while (
        time.time() - started
        < timeout_seconds
    ):

        result = http_json(
            "GET",
            f"{RUNWAY_API_BASE}/tasks/{task_id}",
            headers=runway_headers()
        )

        status = str(
            result.get(
                "status",
                ""
            )
        ).upper()

        if status == "SUCCEEDED":

            output = result.get(
                "output"
            ) or []

            if not output:

                raise RuntimeError(
                    "Runway returned no image."
                )

            return output[0]

        if status in {
            "FAILED",
            "CANCELED",
            "CANCELLED"
        }:

            raise RuntimeError(
                f"Runway failed: {result}"
            )

        time.sleep(5)

    raise RuntimeError(
        f"Runway timed out: {task_id}"
    )


def generate_image(
    prompt,
    output_path
):

    safe_prompt = clean_prompt(
        prompt
    )

    task_id = create_runway_image(
        safe_prompt
    )

    image_url = wait_for_runway_image(
        task_id
    )

    download_file(
        image_url,
        output_path
    )

    return {
        "task_id": task_id,
        "prompt_length": len(
            safe_prompt
        ),
        "image_url": image_url,
        "local_file": str(
            output_path
        )
    }


# ============================================================
# COSMO CREW
# ============================================================

NIA = (
    "Nia, a cheerful young Black girl astronaut "
    "with two natural puff ponytails"
)

MATEO = (
    "Mateo, a cheerful young Latino boy astronaut "
    "with short dark hair"
)

ANAYA = (
    "Anaya, a cheerful young South Asian girl astronaut "
    "with long dark hair"
)


BASE_STYLE = """
Premium black-and-white children's coloring-book illustration.
Polished modern kawaii children's art, expressive friendly characters,
smooth bold professional outlines, pure white background, no color,
no gray, no shading, no gradients, no text, no letters, no numbers,
no watermark. Futuristic child-friendly astronaut suits. Full portrait
composition with an imaginative space environment. Large open coloring
areas while retaining premium illustrated detail. Professional commercial
coloring-book quality.
"""


# ============================================================
# 28-PAGE BOOK PLAN
# ============================================================

def build_page_plan(payload):

    world = payload.get(
        "world",
        "futuristic space adventure"
    )

    return [

        {
            "title":
                "MEET THE COSMO CREW",

            "instruction":
                "Three friends. One universe of learning adventures.",

            "prompt":
                f"""
                {NIA}, {MATEO}, and {ANAYA}
                stand together inside a beautiful futuristic
                space observatory. Curved observation window,
                planets, stars, friendly rocket, control panels
                and magical distant space city. Full-body group
                portrait. {world}.
                """
        },

        {
            "title":
                "COLOR + COUNT: NUMBER 1",

            "instruction":
                "Find and color the ONE special cosmic flower.",

            "prompt":
                f"""
                {NIA} explores a magical moon garden beside
                EXACTLY ONE large star-shaped cosmic flower.
                Include moon rocks, one friendly alien and a
                distant rocket. The flower is the obvious
                counting object.
                """
        },

        {
            "title":
                "COLOR + COUNT: NUMBER 2",

            "instruction":
                "Find and color the TWO big planets.",

            "prompt":
                f"""
                {MATEO} flies with a child-friendly jetpack beside
                EXACTLY TWO large planets, one ringed and one
                cratered. Include a distant rocket, crescent moon
                and futuristic space station.
                """
        },

        {
            "title":
                "COLOR + COUNT: NUMBER 3",

            "instruction":
                "Count and color THREE friendly aliens.",

            "prompt":
                f"""
                {ANAYA} discovers EXACTLY THREE cute friendly
                aliens standing on a moon landscape beside their
                tiny spaceship. The three aliens are clearly
                separated and easy to count.
                """
        },

        {
            "title":
                "COLOR + COUNT: NUMBER 4",

            "instruction":
                "Count FOUR space crystals.",

            "prompt":
                f"""
                {NIA} explores a crystal cave on a friendly alien
                planet containing EXACTLY FOUR large cosmic
                crystals. Make the four crystals obvious and
                clearly separated.
                """
        },

        {
            "title":
                "COLOR + COUNT: NUMBER 5",

            "instruction":
                "Find FIVE rocket ships.",

            "prompt":
                f"""
                {MATEO} watches EXACTLY FIVE cute small rocket
                ships flying through a whimsical spaceport.
                Make all five rockets clearly visible and easy
                to count.
                """
        },

        {
            "title":
                "COLOR + COUNT: NUMBER 6",

            "instruction":
                "Count SIX moon rocks.",

            "prompt":
                f"""
                {ANAYA} explores a moon valley containing EXACTLY
                SIX large interesting moon rocks. Include a rover
                and distant planet. Make all six rocks clearly
                separated.
                """
        },

        {
            "title":
                "COLOR + COUNT: NUMBER 7",

            "instruction":
                "Find SEVEN cosmic gems.",

            "prompt":
                f"""
                {NIA} opens a futuristic treasure chest containing
                EXACTLY SEVEN large cosmic gems. Friendly alien
                companion nearby. Gems must be clearly countable.
                """
        },

        {
            "title":
                "COLOR + COUNT: NUMBER 8",

            "instruction":
                "Count EIGHT space bubbles.",

            "prompt":
                f"""
                {MATEO} floats inside a zero-gravity space station
                surrounded by EXACTLY EIGHT large floating cosmic
                bubbles. Make every bubble distinct and countable.
                """
        },

        {
            "title":
                "COLOR + COUNT: NUMBER 9",

            "instruction":
                "Find NINE glowing moons.",

            "prompt":
                f"""
                {ANAYA} looks through a futuristic telescope at
                EXACTLY NINE small moons arranged clearly in the
                sky. Observatory environment and friendly space
                scenery.
                """
        },

        {
            "title":
                "COLOR + COUNT: NUMBER 10",

            "instruction":
                "Count TEN shooting stars.",

            "prompt":
                f"""
                {NIA}, {MATEO}, and {ANAYA} watch EXACTLY TEN
                large shooting stars from a moon lookout.
                Make all ten clearly visible and countable.
                """
        },

        {
            "title":
                "TRACE THE ROCKET PATH",

            "instruction":
                "Follow the path through space.",

            "prompt":
                f"""
                {MATEO} pilots a cute rocket through a whimsical
                asteroid field. Create a clear winding open pathway
                from the rocket toward a friendly planet. Fun
                maze-like learning composition.
                """
        },

        {
            "title":
                "FIND THE MATCH",

            "instruction":
                "Which two planets look alike?",

            "prompt":
                f"""
                {ANAYA} studies four large friendly planets in a
                space laboratory. Two planets share the same
                distinctive ring and crater pattern while the
                others are visibly different.
                """
        },

        {
            "title":
                "BIG OR SMALL?",

            "instruction":
                "Find the biggest rocket.",

            "prompt":
                f"""
                {NIA} stands beside three rockets of clearly
                different sizes: small, medium and very large.
                Futuristic launch pad environment.
                """
        },

        {
            "title":
                "FIND THE DIFFERENT ALIEN",

            "instruction":
                "Which alien is different?",

            "prompt":
                f"""
                {MATEO} meets four cute friendly aliens. Three
                aliens have matching antenna shapes and one has
                clearly different antennae. Fun visual discovery
                activity.
                """
        },

        {
            "title":
                "COUNT THE ROCKET WINDOWS",

            "instruction":
                "How many windows can you find?",

            "prompt":
                f"""
                {ANAYA} stands beside a large whimsical rocket
                with several big round windows. Futuristic launch
                platform, stars and planets. Windows should be
                large and easy to identify.
                """
        },

        {
            "title":
                "SPACE SHAPES",

            "instruction":
                "Find circles, squares and triangles.",

            "prompt":
                f"""
                {NIA} explores a futuristic control room filled
                with clearly recognizable circle, square and
                triangle shaped buttons and objects. Friendly
                educational space environment.
                """
        },

        {
            "title":
                "WHAT COMES NEXT?",

            "instruction":
                "Discover the repeating space pattern.",

            "prompt":
                """
                A playful space scene showing a clear repeating
                visual pattern using rocket, planet, rocket,
                planet, rocket, with an open final position.
                Include a friendly astronaut observing the pattern.
                """
        },

        {
            "title":
                "COUNT THE ALIEN PETS",

            "instruction":
                "How many cosmic pets are playing?",

            "prompt":
                f"""
                {MATEO} plays with several cute alien pets in a
                futuristic moon park. Each pet is clearly separated
                and visually distinct for counting.
                """
        },

        {
            "title":
                "SPACE TREASURE HUNT",

            "instruction":
                "Find the hidden cosmic treasures.",

            "prompt":
                f"""
                {ANAYA} searches a whimsical alien landscape for
                large hidden objects: crystal, rocket toy, moon
                gem and astronaut badge. Objects remain visible
                enough for a young child to discover.
                """
        },

        {
            "title":
                "FINISH THE ROCKET",

            "instruction":
                "Imagine what the rocket needs next.",

            "prompt":
                f"""
                {NIA} stands beside a large partially assembled
                futuristic rocket in a friendly workshop.
                Several simple rocket components sit nearby.
                Creative learning scene.
                """
        },

        {
            "title":
                "MOON MAZE",

            "instruction":
                "Help Mateo reach the rocket.",

            "prompt":
                f"""
                {MATEO} stands on one side of a playful moon
                landscape and his rocket waits on the other.
                A clear child-friendly winding pathway travels
                between moon rocks toward the rocket.
                """
        },

        {
            "title":
                "COUNT THE PLANETS",

            "instruction":
                "How many planets can you discover?",

            "prompt":
                f"""
                {ANAYA} travels through a beautiful solar-system
                scene containing several large visually distinct
                planets. Planets are separated and easy to count.
                Premium space coloring composition.
                """
        },

        {
            "title":
                "COSMO CREW CELEBRATION",

            "instruction":
                "Color the crew's space celebration.",

            "prompt":
                f"""
                {NIA}, {MATEO}, and {ANAYA} celebrate completing
                their number adventure inside a futuristic space
                station. Friendly aliens cheer with them, planets
                visible through a large window.
                """
        },

        {
            "title":
                "MY SPACE MASTERPIECE",

            "instruction":
                "Add your own colors and imagination.",

            "prompt":
                f"""
                {NIA}, {MATEO}, and {ANAYA} explore a magical
                panoramic alien world with rockets, planets,
                friendly creatures and futuristic architecture.
                Beautiful finale coloring scene.
                """
        },

        {
            "title":
                "COSMO CREW GRADUATE",

            "instruction":
                "You completed your Space Numbers Adventure!",

            "prompt":
                f"""
                {NIA}, {MATEO}, and {ANAYA} proudly hold a large
                blank futuristic achievement plaque together.
                Stars, planets and a rocket surround them.
                Celebratory coloring-book composition. No text
                inside the plaque.
                """
        },

        {
            "title":
                "REACH FOR THE STARS",

            "instruction":
                "Keep learning. Keep exploring. Keep dreaming.",

            "prompt":
                f"""
                {NIA}, {MATEO}, and {ANAYA} stand together on a
                peaceful moon hill looking toward a giant beautiful
                ringed planet, stars and distant galaxies.
                Inspirational magical final scene.
                """
        },

        {
            "title":
                "YOUR NEXT ADVENTURE",

            "instruction":
                "The Cosmo Crew will return in Alphabet Adventure!",

            "prompt":
                f"""
                {NIA}, {MATEO}, and {ANAYA} walk toward a glowing
                futuristic portal leading to their next cosmic
                learning adventure. Friendly rocket nearby,
                exciting new planet visible beyond the portal.
                No text.
                """
        }
    ]


# ============================================================
# PDF
# ============================================================

def draw_title(
    c,
    width,
    height,
    title,
    instruction
):

    c.setFont(
        "Helvetica-Bold",
        18
    )

    c.drawCentredString(
        width / 2,
        height - 0.48 * inch,
        title
    )

    c.setFont(
        "Helvetica",
        10.5
    )

    c.drawCentredString(
        width / 2,
        height - 0.75 * inch,
        instruction
    )


def place_image(
    c,
    image_path,
    width,
    height
):

    image = ImageReader(
        str(image_path)
    )

    sw, sh = image.getSize()

    available_width = (
        width - 0.55 * inch
    )

    available_height = (
        height - 1.20 * inch
    )

    scale = min(
        available_width / sw,
        available_height / sh
    )

    dw = sw * scale
    dh = sh * scale

    x = (
        width - dw
    ) / 2

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


def create_pdf(
    path,
    payload,
    page_plan,
    images
):

    width = float(
        payload.get(
            "trim_width",
            8.5
        )
    ) * inch

    height = float(
        payload.get(
            "trim_height",
            11
        )
    ) * inch

    c = canvas.Canvas(
        str(path),
        pagesize=(
            width,
            height
        )
    )

    for page, image_path in zip(
        page_plan,
        images
    ):

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
# METADATA
# ============================================================

def create_metadata(
    path,
    payload,
    job_id,
    page_plan,
    results
):

    metadata = {

        "job_id": job_id,

        "brand":
            payload.get("brand"),

        "imprint":
            payload.get("imprint"),

        "series":
            payload.get("series"),

        "topic":
            payload.get("topic"),

        "volume":
            payload.get("volume"),

        "age_band":
            payload.get("age_band"),

        "page_count":
            len(page_plan),

        "publish_enabled":
            False,

        "runway_model":
            RUNWAY_IMAGE_MODEL,

        "runway_ratio":
            RUNWAY_IMAGE_RATIO,

        "pages":
            page_plan,

        "runway_tasks":
            results,

        "next_book":
            "Cosmo Crew: Alphabet Adventure",

        "production_status":
            "FULL 28 PAGE PREVIEW",

        "seasonal_content":
            False
    }

    path.write_text(
        json.dumps(
            metadata,
            indent=2
        ),
        encoding="utf-8"
    )


# ============================================================
# BUILD FULL BOOK
# ============================================================

def build_job(
    job_id,
    payload,
    base_url
):

    try:

        jobs[job_id]["status"] = "RUNNING"

        folder = get_job_dir(
            job_id
        )

        page_plan = build_page_plan(
            payload
        )

        images = []
        results = []

        total = len(
            page_plan
        )

        for index, page in enumerate(
            page_plan,
            start=1
        ):

            jobs[job_id][
                "progress"
            ] = (
                f"Generating page "
                f"{index} of {total}: "
                f"{page['title']}"
            )

            image_path = (
                folder
                / f"page_{index:02d}.png"
            )

            full_prompt = (
                BASE_STYLE
                + "\n"
                + page["prompt"]
            )

            result = generate_image(
                full_prompt,
                image_path
            )

            images.append(
                image_path
            )

            results.append(
                result
            )

            jobs[job_id][
                "pages_completed"
            ] = index

            jobs[job_id][
                "pages_total"
            ] = total

        interior_pdf = (
            folder
            / "cosmo_crew_space_numbers_full_preview.pdf"
        )

        metadata_file = (
            folder
            / "metadata.json"
        )

        package_file = (
            folder
            / "cosmo_crew_space_numbers_package.zip"
        )

        jobs[job_id][
            "progress"
        ] = "Building 28-page interior PDF"

        create_pdf(
            interior_pdf,
            payload,
            page_plan,
            images
        )

        create_metadata(
            metadata_file,
            payload,
            job_id,
            page_plan,
            results
        )

        jobs[job_id][
            "progress"
        ] = "Packaging book files"

        with zipfile.ZipFile(
            package_file,
            "w",
            zipfile.ZIP_DEFLATED
        ) as archive:

            archive.write(
                interior_pdf,
                interior_pdf.name
            )

            archive.write(
                metadata_file,
                metadata_file.name
            )

            for image in images:

                archive.write(
                    image,
                    image.name
                )

        jobs[job_id].update(
            {
                "status":
                    "SUCCEEDED",

                "progress":
                    "Full 28-page preview ready",

                "publish_enabled":
                    False,

                "page_count":
                    total,

                "pages_completed":
                    total,

                "pages_total":
                    total,

                "interior_pdf_url":
                    f"{base_url}/files/{job_id}/{interior_pdf.name}",

                "metadata_url":
                    f"{base_url}/files/{job_id}/{metadata_file.name}",

                "package_url":
                    f"{base_url}/files/{job_id}/{package_file.name}"
            }
        )

    except Exception as e:

        jobs[job_id].update(
            {
                "status":
                    "FAILED",

                "progress":
                    "Book generation failed",

                "error":
                    repr(e)
            }
        )


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():

    return {
        "service":
            "Cosmo Crew KDP Book Factory",

        "version":
            "3.0.0",

        "status":
            "ready",

        "runway_enabled":
            bool(
                RUNWAYML_API_SECRET
            ),

        "runway_model":
            RUNWAY_IMAGE_MODEL,

        "book_pages":
            28,

        "publish_enabled":
            False
    }


@app.get("/health")
def health():

    return {
        "ok":
            True,

        "version":
            "3.0.0",

        "runway_enabled":
            bool(
                RUNWAYML_API_SECRET
            ),

        "full_book_engine":
            True,

        "pages":
            28
    }


@app.post("/jobs")
def create_job(
    payload: JobPayload,
    request: Request,
    x_builder_key: str | None = Header(
        default=None
    )
):

    authorize(
        x_builder_key
    )

    if payload.publish:

        raise HTTPException(
            status_code=400,
            detail=(
                "Amazon publishing is still "
                "disabled during preview testing."
            )
        )

    job_id = (
        uuid.uuid4()
        .hex[:12]
    )

    base_url = str(
        request.base_url
    ).rstrip("/")

    jobs[job_id] = {

        "job_id":
            job_id,

        "status":
            "QUEUED",

        "progress":
            "Preparing 28-page book",

        "pages_completed":
            0,

        "pages_total":
            28,

        "created_at":
            time.time(),

        "status_url":
            f"{base_url}/status/{job_id}",

        "publish_enabled":
            False
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
    x_builder_key: str | None = Header(
        default=None
    )
):

    authorize(
        x_builder_key
    )

    if job_id not in jobs:

        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    return jobs[job_id]


@app.get("/files/{job_id}/{filename}")
def get_file(
    job_id,
    filename
):

    file_path = (
        DATA_DIR
        / job_id
        / filename
    ).resolve()

    if not str(
        file_path
    ).startswith(
        str(
            DATA_DIR.resolve()
        )
    ):

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
