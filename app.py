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

# For this first REAL ART test, use standard Gen-4 Image.
# After the visual quality is approved we can optimize cost/model.
RUNWAY_IMAGE_MODEL = os.getenv("RUNWAY_IMAGE_MODEL", "gen4_image")

# Portrait output for coloring-book artwork.
RUNWAY_IMAGE_RATIO = os.getenv("RUNWAY_IMAGE_RATIO", "720:1280")

RUNWAY_API_BASE = "https://api.dev.runwayml.com/v1"
RUNWAY_API_VERSION = "2024-11-06"

app = FastAPI(
    title="KDP Coloring Book Builder",
    version="2.0.0"
)

jobs: Dict[str, Dict[str, Any]] = {}


# ============================================================
# INPUT MODEL
# ============================================================

class JobPayload(BaseModel):
    mode: str = "TEST_PREVIEW"
    publish: bool = False

    kind: str = "kids"

    brand: str = "YOUR PUBLISHER BRAND"
    imprint: str = "Cosmo Crew Learning Adventures"

    series: str = "Space Adventure"
    world: str = (
        "futuristic space stations, friendly planets, rockets, "
        "moons, stars, observatories"
    )

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

def authorize(x_builder_key: str | None):
    if not x_builder_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Builder-Key"
        )

    if x_builder_key != BUILDER_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid X-Builder-Key"
        )


# ============================================================
# HELPERS
# ============================================================

def get_job_dir(job_id: str) -> Path:
    folder = DATA_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def runway_headers():
    return {
        "Authorization": f"Bearer {RUNWAYML_API_SECRET}",
        "Content-Type": "application/json",
        "X-Runway-Version": RUNWAY_API_VERSION,
    }


def http_json(
    method: str,
    url: str,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: int = 120,
):
    body = None

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url=url,
        data=body,
        headers=headers or {},
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")

            if not raw:
                return {}

            return json.loads(raw)

    except urllib.error.HTTPError as e:
        error_body = e.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"HTTP {e.code} from {url}: {error_body}"
        )

    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Network error calling {url}: {e}"
        )


def download_file(url: str, destination: Path):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urllib.request.urlopen(req, timeout=180) as response:
        destination.write_bytes(response.read())


# ============================================================
# RUNWAY
# ============================================================

def create_runway_image(prompt: str) -> str:
    if not RUNWAYML_API_SECRET:
        raise RuntimeError(
            "RUNWAYML_API_SECRET is missing from Railway Variables."
        )

    payload = {
        "model": RUNWAY_IMAGE_MODEL,
        "ratio": RUNWAY_IMAGE_RATIO,
        "promptText": prompt,
    }

    response = http_json(
        method="POST",
        url=f"{RUNWAY_API_BASE}/text_to_image",
        payload=payload,
        headers=runway_headers(),
        timeout=120,
    )

    task_id = response.get("id")

    if not task_id:
        raise RuntimeError(
            f"Runway did not return a task id: {response}"
        )

    return task_id


def wait_for_runway_image(
    task_id: str,
    timeout_seconds: int = 420,
) -> str:

    start = time.time()

    while time.time() - start < timeout_seconds:

        result = http_json(
            method="GET",
            url=f"{RUNWAY_API_BASE}/tasks/{task_id}",
            headers=runway_headers(),
            timeout=120,
        )

        status = str(
            result.get("status", "")
        ).upper()

        if status == "SUCCEEDED":

            output = result.get("output") or []

            if not output:
                raise RuntimeError(
                    f"Runway task succeeded but returned no image: {result}"
                )

            return output[0]

        if status in {
            "FAILED",
            "CANCELED",
            "CANCELLED",
        }:
            raise RuntimeError(
                f"Runway image task failed: {result}"
            )

        time.sleep(5)

    raise RuntimeError(
        f"Runway image task timed out: {task_id}"
    )


def generate_image(
    prompt: str,
    output_path: Path,
) -> dict:

    task_id = create_runway_image(prompt)

    image_url = wait_for_runway_image(task_id)

    download_file(
        image_url,
        output_path,
    )

    return {
        "task_id": task_id,
        "image_url": image_url,
        "local_file": str(output_path),
    }


# ============================================================
# ART DIRECTION
# ============================================================

MASTER_ART_DIRECTION = """
Create a premium professional children's coloring-book illustration.

VISUAL QUALITY:
The finished illustration must look like artwork from a high-quality
commercial children's coloring book sold in bookstores and on Amazon.

STYLE:
Cute polished modern kawaii children's illustration.
Warm, expressive, charming characters.
Large friendly eyes.
Natural joyful facial expressions.
Smooth professional black outlines.
Consistent line weight.
Detailed enough to feel premium, but not overcrowded.
Large open areas that children ages 3-5 can comfortably color.

BLACK AND WHITE ONLY:
Pure white background.
Crisp black line art.
NO color.
NO gray.
NO grayscale.
NO shading.
NO hatching.
NO gradients.
NO filled black backgrounds.

IMPORTANT:
Do NOT draw words.
Do NOT draw letters.
Do NOT draw numbers.
Do NOT draw captions.
Do NOT draw watermarks.
Do NOT draw page borders.
Typography will be added separately by the book-building system.

CHARACTERS:
The recurring Cosmo Crew are young child space explorers.

NIA:
Young Black girl.
Dark skin.
Two rounded natural puff ponytails.
Bright expressive eyes.
Friendly adventurous personality.
Futuristic child astronaut suit.

MATEO:
Young Latino boy.
Warm medium skin.
Short dark hair.
Bright expressive eyes.
Confident friendly personality.
Futuristic child astronaut suit.

ANAYA:
Young South Asian girl.
Warm brown skin.
Long dark hair styled neatly for an astronaut helmet.
Bright expressive eyes.
Curious joyful personality.
Futuristic child astronaut suit.

All children must look approximately the same age.
They are friends and equals.
Their astronaut suits should share one consistent futuristic design language.

WORLD:
Whimsical futuristic space adventure.
Friendly planets.
Stars.
Moons.
Rockets.
Space stations.
Observatories.
Cute cosmic companions.
Imaginative sci-fi environments designed specifically for children.

The composition must fill most of the portrait page.
Avoid huge empty areas.
Every page should feel like a complete illustrated scene.
"""


# ============================================================
# THREE TEST PAGE PROMPTS
# ============================================================

def prompt_meet_the_crew() -> str:
    return MASTER_ART_DIRECTION + """

SCENE:
A beautiful introductory group portrait of Nia, Mateo, and Anaya
standing together as the Cosmo Crew.

They are inside an incredible child-friendly futuristic space
observatory.

Behind them:
a giant curved observation window,
Saturn-like planets,
stars,
a friendly small rocket,
futuristic control panels,
and a magical distant space city.

Nia stands proudly on the left.
Mateo stands confidently in the center.
Anaya stands cheerfully on the right.

All three children are smiling and ready for adventure.

Make this feel like the opening illustration of a premium children's
coloring-book series.

Portrait composition.
Full-body characters.
Rich environment.
Large colorable areas.
No words or numbers anywhere.
"""


def prompt_number_one() -> str:
    return MASTER_ART_DIRECTION + """

LEARNING SCENE — NUMBER ONE CONCEPT:

Nia is exploring a magical moon garden.

She is kneeling beside EXACTLY ONE large friendly star-shaped cosmic
flower.

The single star flower is the obvious central counting object.

Around her:
a small moon landscape,
a distant rocket,
planet rings in the sky,
tiny decorative sparkles,
space rocks,
and a cute little alien companion.

IMPORTANT COUNTING REQUIREMENT:
There must be EXACTLY ONE large star-shaped flower.
Do not add any other star-shaped objects that could confuse the count.

The illustration should naturally teach the concept of ONE while still
looking like an exciting full-page coloring scene.

Do not write the numeral 1.
Do not write any text.

Portrait full-page composition.
Premium detailed coloring-book art.
"""


def prompt_number_two() -> str:
    return MASTER_ART_DIRECTION + """

LEARNING SCENE — NUMBER TWO CONCEPT:

Mateo is flying happily through a whimsical space scene using a small
child astronaut jetpack.

Beside him are EXACTLY TWO large friendly planets.

One planet has rings.
One planet has craters.

The TWO planets are the obvious counting objects.

Include:
a beautiful rocket in the distance,
a crescent moon,
small decorative cosmic sparkles,
and a futuristic space station far below.

IMPORTANT COUNTING REQUIREMENT:
There must be EXACTLY TWO large planets.
Do not add additional planet-like circular objects.

The composition should feel adventurous, polished, exciting, and
high-value.

Do not write the numeral 2.
Do not write any text.

Portrait full-page composition.
Premium detailed children's coloring-book art.
"""


# ============================================================
# PDF LAYOUT
# ============================================================

def draw_title_area(
    c,
    width,
    height,
    title,
    instruction,
):
    c.setFont(
        "Helvetica-Bold",
        19,
    )

    c.drawCentredString(
        width / 2,
        height - 0.55 * inch,
        title,
    )

    c.setFont(
        "Helvetica",
        11,
    )

    c.drawCentredString(
        width / 2,
        height - 0.83 * inch,
        instruction,
    )


def place_image_on_page(
    c,
    image_path: Path,
    width,
    height,
):
    image = ImageReader(
        str(image_path)
    )

    source_width, source_height = image.getSize()

    available_width = width - 0.7 * inch
    available_height = height - 1.45 * inch

    scale = min(
        available_width / source_width,
        available_height / source_height,
    )

    draw_width = source_width * scale
    draw_height = source_height * scale

    x = (
        width - draw_width
    ) / 2

    y = 0.42 * inch

    c.drawImage(
        image,
        x,
        y,
        width=draw_width,
        height=draw_height,
        preserveAspectRatio=True,
        mask="auto",
    )


def create_three_page_preview(
    path: Path,
    payload: dict,
    image_paths: list[Path],
):
    width = float(
        payload.get(
            "trim_width",
            8.5,
        )
    ) * inch

    height = float(
        payload.get(
            "trim_height",
            11,
        )
    ) * inch

    c = canvas.Canvas(
        str(path),
        pagesize=(
            width,
            height,
        ),
    )

    # PAGE 1
    draw_title_area(
        c,
        width,
        height,
        "MEET THE COSMO CREW",
        "Three friends. One universe of learning adventures.",
    )

    place_image_on_page(
        c,
        image_paths[0],
        width,
        height,
    )

    c.showPage()

    # PAGE 2
    draw_title_area(
        c,
        width,
        height,
        "COLOR + COUNT: NUMBER 1",
        "Find and color the ONE special cosmic flower.",
    )

    place_image_on_page(
        c,
        image_paths[1],
        width,
        height,
    )

    c.showPage()

    # PAGE 3
    draw_title_area(
        c,
        width,
        height,
        "COLOR + COUNT: NUMBER 2",
        "Find and color the TWO big planets.",
    )

    place_image_on_page(
        c,
        image_paths[2],
        width,
        height,
    )

    c.showPage()

    c.save()


# ============================================================
# METADATA
# ============================================================

def create_metadata(
    path: Path,
    payload: dict,
    job_id: str,
    runway_results: list,
):
    metadata = {
        "job_id": job_id,

        "test_only": True,

        "publish_enabled": False,

        "series": payload.get(
            "series",
            "Space Adventure",
        ),

        "topic": payload.get(
            "topic",
            "Numbers 1-10",
        ),

        "age_band": payload.get(
            "age_band",
            "3-5",
        ),

        "preview_pages_generated": 3,

        "target_final_page_count": payload.get(
            "page_count_target",
            28,
        ),

        "runway_model": RUNWAY_IMAGE_MODEL,

        "runway_ratio": RUNWAY_IMAGE_RATIO,

        "runway_tasks": runway_results,

        "notes": [
            "REAL ART TEST ONLY.",
            "Nothing is uploaded to Amazon KDP.",
            "Three pages are generated to approve visual quality before generating the full book.",
            "Typography is added separately from the AI artwork.",
            "No holiday or seasonal content is used.",
        ],
    }

    path.write_text(
        json.dumps(
            metadata,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# JOB PROCESSOR
# ============================================================

def build_job(
    job_id: str,
    payload: dict,
    base_url: str,
):
    try:

        jobs[job_id]["status"] = "RUNNING"

        folder = get_job_dir(
            job_id
        )

        page1 = folder / "page_01_meet_cosmo_crew.png"
        page2 = folder / "page_02_number_one.png"
        page3 = folder / "page_03_number_two.png"

        preview_pdf = folder / "real_art_preview.pdf"
        metadata_file = folder / "metadata.json"
        package_file = folder / "real_art_preview_package.zip"

        jobs[job_id][
            "progress"
        ] = "Generating Meet the Cosmo Crew"

        result1 = generate_image(
            prompt_meet_the_crew(),
            page1,
        )

        jobs[job_id][
            "progress"
        ] = "Generating Number 1 activity"

        result2 = generate_image(
            prompt_number_one(),
            page2,
        )

        jobs[job_id][
            "progress"
        ] = "Generating Number 2 activity"

        result3 = generate_image(
            prompt_number_two(),
            page3,
        )

        runway_results = [
            result1,
            result2,
            result3,
        ]

        jobs[job_id][
            "progress"
        ] = "Building preview PDF"

        create_three_page_preview(
            preview_pdf,
            payload,
            [
                page1,
                page2,
                page3,
            ],
        )

        create_metadata(
            metadata_file,
            payload,
            job_id,
            runway_results,
        )

        with zipfile.ZipFile(
            package_file,
            "w",
            zipfile.ZIP_DEFLATED,
        ) as archive:

            archive.write(
                page1,
                page1.name,
            )

            archive.write(
                page2,
                page2.name,
            )

            archive.write(
                page3,
                page3.name,
            )

            archive.write(
                preview_pdf,
                preview_pdf.name,
            )

            archive.write(
                metadata_file,
                metadata_file.name,
            )

        jobs[job_id].update(
            {
                "status": "SUCCEEDED",

                "progress": "Preview ready",

                "publish_enabled": False,

                "preview_pages_generated": 3,

                "target_final_page_count": payload.get(
                    "page_count_target",
                    28,
                ),

                "interior_pdf_url":
                    f"{base_url}/files/{job_id}/{preview_pdf.name}",

                "page_1_url":
                    f"{base_url}/files/{job_id}/{page1.name}",

                "page_2_url":
                    f"{base_url}/files/{job_id}/{page2.name}",

                "page_3_url":
                    f"{base_url}/files/{job_id}/{page3.name}",

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
                "progress": "Generation failed",
                "error": repr(e),
            }
        )


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "service": "KDP Coloring Book Builder",
        "version": "2.0.0",
        "status": "ready",
        "runway_enabled": bool(
            RUNWAYML_API_SECRET
        ),
        "model": RUNWAY_IMAGE_MODEL,
        "ratio": RUNWAY_IMAGE_RATIO,
        "test_only": True,
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "kdp-book-builder",
        "version": "2.0.0",
        "runway_enabled": bool(
            RUNWAYML_API_SECRET
        ),
        "model": RUNWAY_IMAGE_MODEL,
        "ratio": RUNWAY_IMAGE_RATIO,
    }


@app.post("/jobs")
def create_job(
    payload: JobPayload,
    request: Request,
    x_builder_key: str | None = Header(
        default=None
    ),
):
    authorize(
        x_builder_key
    )

    if payload.publish:
        raise HTTPException(
            status_code=400,
            detail=(
                "Publishing is disabled "
                "in TEST mode."
            ),
        )

    job_id = uuid.uuid4().hex[:12]

    base_url = str(
        request.base_url
    ).rstrip("/")

    jobs[job_id] = {
        "job_id": job_id,

        "status": "QUEUED",

        "progress": "Waiting to start",

        "created_at": time.time(),

        "status_url":
            f"{base_url}/status/{job_id}",

        "publish_enabled": False,
    }

    thread = threading.Thread(
        target=build_job,
        args=(
            job_id,
            payload.model_dump(),
            base_url,
        ),
        daemon=True,
    )

    thread.start()

    return jobs[job_id]


@app.get("/status/{job_id}")
def get_status(
    job_id: str,
    x_builder_key: str | None = Header(
        default=None
    ),
):
    authorize(
        x_builder_key
    )

    if job_id not in jobs:
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    return jobs[job_id]


@app.get("/files/{job_id}/{filename}")
def get_file(
    job_id: str,
    filename: str,
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
            detail="Invalid path",
        )

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail="File not found",
        )

    return FileResponse(
        str(file_path),
        filename=filename,
    )
