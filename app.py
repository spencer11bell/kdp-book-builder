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
    version="2.1.0"
)

jobs: Dict[str, Dict[str, Any]] = {}


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
        body = json.dumps(
            payload
        ).encode("utf-8")

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

            raw = response.read().decode(
                "utf-8"
            )

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


def download_file(
    url: str,
    destination: Path
):
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


def create_runway_image(
    prompt: str
) -> str:

    if not RUNWAYML_API_SECRET:
        raise RuntimeError(
            "RUNWAYML_API_SECRET is missing."
        )

    safe_prompt = clean_prompt(
        prompt
    )

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

    task_id = response.get(
        "id"
    )

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

    while (
        time.time() - started
        < timeout_seconds
    ):

        result = http_json(
            method="GET",
            url=f"{RUNWAY_API_BASE}/tasks/{task_id}",
            headers=runway_headers(),
            timeout=120,
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
                    "Runway succeeded but returned no image."
                )

            return output[0]

        if status in {
            "FAILED",
            "CANCELED",
            "CANCELLED",
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
        ),
    }


def prompt_meet_the_crew() -> str:
    return """
Premium black-and-white children's coloring-book illustration, polished kawaii style, smooth bold outlines, pure white background, no shading, no gray, no color, no text. Three recurring child astronauts stand together in a futuristic space observatory. Nia: young Black girl with two natural puff ponytails. Mateo: young Latino boy with short dark hair. Anaya: young South Asian girl with long dark hair. All are the same age, cheerful and expressive, wearing matching futuristic astronaut suits. Behind them: curved space window, planets, stars, friendly rocket, futuristic controls, distant magical space city. Full-body portrait composition, detailed but easy for ages 3-5 to color, large open coloring areas, professional commercial coloring-book quality.
"""


def prompt_number_one() -> str:
    return """
Premium black-and-white children's coloring-book illustration, polished kawaii style, smooth bold outlines, pure white background, no shading, no gray, no color, no text or numbers. Nia, a cheerful young Black girl astronaut with two natural puff ponytails, explores a magical moon garden. She kneels beside EXACTLY ONE large star-shaped cosmic flower, clearly the main counting object. Include a moon landscape, distant rocket, ringed planet in the sky, space rocks and one cute alien companion. Do not include any other star-shaped objects. Full portrait scene, expressive character, rich environment, large open coloring areas, professional children's coloring-book quality for ages 3-5.
"""


def prompt_number_two() -> str:
    return """
Premium black-and-white children's coloring-book illustration, polished kawaii style, smooth bold outlines, pure white background, no shading, no gray, no color, no text or numbers. Mateo, a cheerful young Latino boy astronaut with short dark hair, flies through space with a child-friendly jetpack beside EXACTLY TWO large planets. One planet has rings and one has craters. These are the only large planets. Include a distant rocket, crescent moon, small cosmic sparkles and futuristic space station below. Adventurous portrait composition, expressive character, detailed but easy for ages 3-5 to color, large open coloring areas, professional commercial coloring-book quality.
"""


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
        str(
            image_path
        )
    )

    source_width, source_height = (
        image.getSize()
    )

    available_width = (
        width - 0.55 * inch
    )

    available_height = (
        height - 1.25 * inch
    )

    scale = min(
        available_width
        / source_width,
        available_height
        / source_height
    )

    draw_width = (
        source_width * scale
    )

    draw_height = (
        source_height * scale
    )

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

    c = canvas.Canvas(
        str(
            path
        ),
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
        "Three friends. One universe of learning adventures."
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


def create_metadata(
    path: Path,
    payload: dict,
    job_id: str,
    runway_results: list
):
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
        "age_band": payload.get(
            "age_band",
            "3-5"
        ),
        "preview_pages_generated": 3,
        "target_final_page_count": payload.get(
            "page_count_target",
            28
        ),
        "runway_model": RUNWAY_IMAGE_MODEL,
        "runway_ratio": RUNWAY_IMAGE_RATIO,
        "runway_tasks": runway_results,
        "notes": [
            "REAL ART TEST ONLY.",
            "Nothing is uploaded to Amazon KDP.",
            "Three pages only for visual approval.",
            "AI image artwork contains no typography.",
            "All text is added separately in the PDF builder.",
            "No holiday or seasonal content."
        ]
    }

    path.write_text(
        json.dumps(
            metadata,
            indent=2
        ),
        encoding="utf-8"
    )


def build_job(
    job_id: str,
    payload: dict,
    base_url: str
):
    try:
        jobs[job_id][
            "status"
        ] = "RUNNING"

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
        ] = "Generating Meet the Cosmo Crew"

        result1 = generate_image(
            prompt_meet_the_crew(),
            page1
        )

        jobs[job_id][
            "progress"
        ] = "Generating Number 1 activity"

        result2 = generate_image(
            prompt_number_one(),
            page2
        )

        jobs[job_id][
            "progress"
        ] = "Generating Number 2 activity"

        result3 = generate_image(
            prompt_number_two(),
            page3
        )

        runway_results = [
            result1,
            result2,
            result3
        ]

        jobs[job_id][
            "progress"
        ] = "Building real-art preview PDF"

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


@app.get("/")
def root():
    return {
        "service": "KDP Coloring Book Builder",
        "version": "2.1.0",
        "status": "ready",
        "runway_enabled": bool(
            RUNWAYML_API_SECRET
        ),
        "model": RUNWAY_IMAGE_MODEL,
        "ratio": RUNWAY_IMAGE_RATIO,
        "test_only": True
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "kdp-book-builder",
        "version": "2.1.0",
        "runway_enabled": bool(
            RUNWAYML_API_SECRET
        ),
        "model": RUNWAY_IMAGE_MODEL,
        "ratio": RUNWAY_IMAGE_RATIO
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
                "Publishing is disabled "
                "in TEST mode."
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
        "job_id": job_id,
        "status": "QUEUED",
        "progress": "Waiting to start",
        "created_at": time.time(),
        "status_url":
            f"{base_url}/status/{job_id}",
        "publish_enabled": False
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
        str(
            file_path
        ),
        filename=filename
    )
