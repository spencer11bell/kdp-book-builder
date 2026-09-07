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
    title="KDP Coloring Book Builder",
    version="2.2.0"
)

jobs: Dict[str, Dict[str, Any]] = {}


# ============================================================
# INPUT
# ============================================================

class JobPayload(BaseModel):
    mode: str = "TEST_PREVIEW"
    publish: bool = False
    kind: str = "kids"

    brand: str = "YOUR PUBLISHER BRAND"
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
    folder.mkdir(
        parents=True,
        exist_ok=True
    )
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

    request = urllib.request.Request(
        url=url,
        data=body,
        headers=headers or {},
        method=method
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

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
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=180
    ) as response:

        destination.write_bytes(
            response.read()
        )


# ============================================================
# PROMPT SAFETY
# ============================================================

def clean_prompt(text: str) -> str:

    cleaned = " ".join(
        text.split()
    ).strip()

    if len(cleaned) <= MAX_RUNWAY_PROMPT_CHARS:
        return cleaned

    return cleaned[
        :MAX_RUNWAY_PROMPT_CHARS
    ].rsplit(
        " ",
        1
    )[0]


# ============================================================
# AGE ADAPTIVE ART SYSTEM
# ============================================================

def normalize_age_band(age_band: str) -> str:

    value = str(age_band).strip().lower()

    if value in {
        "3-5",
        "3–5",
        "ages 3-5",
        "ages 3–5"
    }:
        return "3-5"

    if value in {
        "5-7",
        "5–7",
        "ages 5-7",
        "ages 5–7"
    }:
        return "5-7"

    if value in {
        "7-9",
        "7–9",
        "ages 7-9",
        "ages 7–9"
    }:
        return "7-9"

    return "3-5"


def get_age_art_profile(age_band: str) -> dict:

    age = normalize_age_band(age_band)

    profiles = {

        "3-5": {
            "label": "BEGINNER",
            "complexity": (
                "Very simple preschool coloring page. "
                "One large main character. "
                "Only 3 to 6 major objects total. "
                "Very thick smooth outlines. "
                "Huge open coloring spaces. "
                "Minimal background detail. "
                "No tiny decorative objects. "
                "No crowded scenery."
            ),
            "activity": (
                "One extremely obvious learning task. "
                "Counting objects must be large and clearly separated."
            )
        },

        "5-7": {
            "label": "INTERMEDIATE",
            "complexity": (
                "Moderately detailed children's coloring page. "
                "One or two main characters. "
                "About 6 to 10 major visual elements. "
                "Bold clean outlines. "
                "Good open coloring areas with some environment detail. "
                "Interesting but not crowded."
            ),
            "activity": (
                "Learning task can include counting, matching, tracing, "
                "finding, comparing or simple problem solving."
            )
        },

        "7-9": {
            "label": "ADVANCED KIDS",
            "complexity": (
                "Detailed premium children's coloring page. "
                "Richer environment and visual storytelling. "
                "About 10 to 16 meaningful visual elements. "
                "Clean medium-weight outlines. "
                "Smaller coloring areas are acceptable. "
                "More background detail while remaining readable."
            ),
            "activity": (
                "Learning task may involve multi-step counting, patterns, "
                "spelling, math, science clues or visual problem solving."
            )
        }
    }

    return profiles[age]


# ============================================================
# RUNWAY
# ============================================================

def create_runway_image(prompt: str) -> str:

    if not RUNWAYML_API_SECRET:
        raise RuntimeError(
            "RUNWAYML_API_SECRET is missing."
        )

    safe_prompt = clean_prompt(prompt)

    payload = {
        "model": RUNWAY_IMAGE_MODEL,
        "ratio": RUNWAY_IMAGE_RATIO,
        "promptText": safe_prompt,
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
            f"Runway returned no task id: {response}"
        )

    return task_id


def wait_for_runway_image(
    task_id: str,
    timeout_seconds: int = 420,
) -> str:

    started = time.time()

    while time.time() - started < timeout_seconds:

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
                    "Runway succeeded but returned no image."
                )

            return output[0]

        if status in {
            "FAILED",
            "CANCELED",
            "CANCELLED"
        }:
            raise RuntimeError(
                f"Runway task failed: {result}"
            )

        time.sleep(5)

    raise RuntimeError(
        f"Runway task timed out: {task_id}"
    )


def generate_image(
    prompt: str,
    output_path: Path
) -> dict:

    safe_prompt = clean_prompt(prompt)

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
        "prompt_length": len(safe_prompt),
        "image_url": image_url,
        "local_file": str(output_path),
    }


# ============================================================
# CORE STYLE
# ============================================================

def base_style(age_band: str) -> str:

    profile = get_age_art_profile(
        age_band
    )

    return f"""
Premium black-and-white children's coloring-book illustration.
Modern cute kawaii style, warm expressive faces, commercial-quality
artwork. Pure white background, crisp black outlines, no color, no gray,
no shading, no gradients, no watermarks, no text, no letters, no numbers.

AGE ART DIRECTION:
{profile["complexity"]}

LEARNING DIRECTION:
{profile["activity"]}

The picture must be enjoyable and easy to color for the stated age.
"""


# ============================================================
# CHARACTERS
# ============================================================

def nia_description() -> str:

    return (
        "Nia is a young Black girl space explorer with dark skin, "
        "two rounded natural puff ponytails, large friendly eyes "
        "and a futuristic child astronaut suit."
    )


def mateo_description() -> str:

    return (
        "Mateo is a young Latino boy space explorer with warm medium "
        "skin, short dark hair, large friendly eyes and a futuristic "
        "child astronaut suit."
    )


def anaya_description() -> str:

    return (
        "Anaya is a young South Asian girl space explorer with warm "
        "brown skin, long dark hair, large friendly eyes and a "
        "futuristic child astronaut suit."
    )


# ============================================================
# TEST PROMPTS
# ============================================================

def prompt_meet_the_crew(age_band: str) -> str:

    return clean_prompt(
        base_style(age_band)
        + f"""
{nia_description()}
{mateo_description()}
{anaya_description()}

Scene: Nia, Mateo and Anaya stand together inside a friendly futuristic
space observatory. They smile proudly as a team. Include one large curved
space window, one ringed planet, one moon and one small rocket.
Keep the characters large and central. Full-body portrait composition.
"""
    )


def prompt_number_one(age_band: str) -> str:

    return clean_prompt(
        base_style(age_band)
        + f"""
{nia_description()}

Learning scene: Nia kneels happily on the moon beside EXACTLY ONE large
star-shaped cosmic flower. The flower must be the obvious counting object.
Include only a simple moon ground, one small rocket in the distance and
one friendly alien companion. Do not include any other stars or
star-shaped objects. Make Nia and the flower large and easy to color.
"""
    )


def prompt_number_two(age_band: str) -> str:

    return clean_prompt(
        base_style(age_band)
        + f"""
{mateo_description()}

Learning scene: Mateo flies gently through space using a small jetpack.
Beside him are EXACTLY TWO large planets. One planet has rings and the
other has craters. These are the only planets. Include one small rocket
far away and a simple crescent moon. Keep Mateo and the two planets large,
clear and easy to color.
"""
    )


# ============================================================
# PDF
# ============================================================

def draw_title_area(
    c,
    width,
    height,
    title,
    instruction
):

    c.setFont(
        "Helvetica-Bold",
        19
    )

    c.drawCentredString(
        width / 2,
        height - 0.50 * inch,
        title
    )

    c.setFont(
        "Helvetica",
        11
    )

    c.drawCentredString(
        width / 2,
        height - 0.78 * inch,
        instruction
    )


def place_image_on_page(
    c,
    image_path: Path,
    width,
    height
):

    image = ImageReader(
        str(image_path)
    )

    source_width, source_height = image.getSize()

    available_width = (
        width - 0.55 * inch
    )

    available_height = (
        height - 1.25 * inch
    )

    scale = min(
        available_width / source_width,
        available_height / source_height
    )

    draw_width = source_width * scale
    draw_height = source_height * scale

    x = (
        width - draw_width
    ) / 2

    y = 0.30 * inch

    c.drawImage(
        image,
        x,
        y,
        width=draw_width,
        height=draw_height,
        preserveAspectRatio=True,
        mask="auto"
    )


def create_three_page_preview(
    path: Path,
    payload: dict,
    image_paths: list[Path]
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

    age_band = normalize_age_band(
        payload.get(
            "age_band",
            "3-5"
        )
    )

    c = canvas.Canvas(
        str(path),
        pagesize=(
            width,
            height
        )
    )

    draw_title_area(
        c,
        width,
        height,
        "MEET THE COSMO CREW",
        f"Space Adventure • Ages {age_band}"
    )

    place_image_on_page(
        c,
        image_paths[0],
        width,
        height
    )

    c.showPage()

    draw_title_area(
        c,
        width,
        height,
        "COLOR + COUNT: NUMBER 1",
        "Find and color the ONE special cosmic flower."
    )

    place_image_on_page(
        c,
        image_paths[1],
        width,
        height
    )

    c.showPage()

    draw_title_area(
        c,
        width,
        height,
        "COLOR + COUNT: NUMBER 2",
        "Find and color the TWO big planets."
    )

    place_image_on_page(
        c,
        image_paths[2],
        width,
        height
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
    runway_results: list
):

    age_band = normalize_age_band(
        payload.get(
            "age_band",
            "3-5"
        )
    )

    profile = get_age_art_profile(
        age_band
    )

    metadata = {

        "job_id": job_id,

        "test_only": True,

        "publish_enabled": False,

        "series": payload.get(
            "series",
            "Space Adventure"
        ),

        "topic": payload.get(
            "topic",
            "Numbers 1-10"
        ),

        "age_band": age_band,

        "art_difficulty":
            profile["label"],

        "preview_pages_generated": 3,

        "target_final_page_count":
            payload.get(
                "page_count_target",
                28
            ),

        "runway_model":
            RUNWAY_IMAGE_MODEL,

        "runway_ratio":
            RUNWAY_IMAGE_RATIO,

        "runway_tasks":
            runway_results,

        "notes": [
            "Age-adaptive artwork system enabled.",
            "Ages 3-5 use simplified coloring scenes.",
            "Ages 5-7 use moderate detail.",
            "Ages 7-9 use richer detail.",
            "Nothing is uploaded to Amazon KDP.",
            "No seasonal or holiday content."
        ]
    }

    path.write_text(
        json.dumps(
            metadata,
            indent=2
        ),
        encoding="utf-8"
    )


# ============================================================
# JOB
# ============================================================

def build_job(
    job_id: str,
    payload: dict,
    base_url: str
):

    try:

        jobs[job_id]["status"] = "RUNNING"

        age_band = normalize_age_band(
            payload.get(
                "age_band",
                "3-5"
            )
        )

        jobs[job_id]["age_band"] = age_band

        folder = get_job_dir(
            job_id
        )

        page1 = (
            folder
            / "page_01_meet_cosmo_crew.png"
        )

        page2 = (
            folder
            / "page_02_number_one.png"
        )

        page3 = (
            folder
            / "page_03_number_two.png"
        )

        preview_pdf = (
            folder
            / "real_art_preview.pdf"
        )

        metadata_file = (
            folder
            / "metadata.json"
        )

        package_file = (
            folder
            / "real_art_preview_package.zip"
        )

        jobs[job_id][
            "progress"
        ] = "Generating age-adapted Cosmo Crew"

        result1 = generate_image(
            prompt_meet_the_crew(
                age_band
            ),
            page1
        )

        jobs[job_id][
            "progress"
        ] = "Generating age-adapted Number 1"

        result2 = generate_image(
            prompt_number_one(
                age_band
            ),
            page2
        )

        jobs[job_id][
            "progress"
        ] = "Generating age-adapted Number 2"

        result3 = generate_image(
            prompt_number_two(
                age_band
            ),
            page3
        )

        runway_results = [
            result1,
            result2,
            result3
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
                page3
            ]
        )

        create_metadata(
            metadata_file,
            payload,
            job_id,
            runway_results
        )

        with zipfile.ZipFile(
            package_file,
            "w",
            zipfile.ZIP_DEFLATED
        ) as archive:

            archive.write(
                page1,
                page1.name
            )

            archive.write(
                page2,
                page2.name
            )

            archive.write(
                page3,
                page3.name
            )

            archive.write(
                preview_pdf,
                preview_pdf.name
            )

            archive.write(
                metadata_file,
                metadata_file.name
            )

        jobs[job_id].update(
            {
                "status": "SUCCEEDED",

                "progress": "Preview ready",

                "publish_enabled": False,

                "age_band": age_band,

                "art_difficulty":
                    get_age_art_profile(
                        age_band
                    )["label"],

                "preview_pages_generated": 3,

                "target_final_page_count":
                    payload.get(
                        "page_count_target",
                        28
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
                    f"{base_url}/files/{job_id}/{package_file.name}"
            }
        )

    except Exception as e:

        jobs[job_id].update(
            {
                "status": "FAILED",
                "progress": "Generation failed",
                "error": repr(e)
            }
        )


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():

    return {
        "service":
            "KDP Coloring Book Builder",

        "version":
            "2.2.0",

        "status":
            "ready",

        "runway_enabled":
            bool(
                RUNWAYML_API_SECRET
            ),

        "model":
            RUNWAY_IMAGE_MODEL,

        "ratio":
            RUNWAY_IMAGE_RATIO,

        "age_adaptive":
            True,

        "test_only":
            True
    }


@app.get("/health")
def health():

    return {
        "ok":
            True,

        "service":
            "kdp-book-builder",

        "version":
            "2.2.0",

        "runway_enabled":
            bool(
                RUNWAYML_API_SECRET
            ),

        "age_adaptive":
            True
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
            detail="Publishing is disabled in TEST mode."
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
            "Waiting to start",

        "age_band":
            normalize_age_band(
                payload.age_band
            ),

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
    job_id: str,
    filename: str
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
