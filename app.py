
import io
import json
import os
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from reportlab.lib.pagesizes import inch
from reportlab.pdfgen import canvas

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

BUILDER_KEY = os.getenv("BUILDER_KEY", "change-me")
PREVIEW_MODE = os.getenv("PREVIEW_MODE", "layout").lower().strip()

app = FastAPI(title="KDP Coloring Book Builder", version="1.0.0")
jobs: Dict[str, Dict[str, Any]] = {}


class JobPayload(BaseModel):
    mode: str = "TEST_PREVIEW"
    publish: bool = False
    kind: str = "kids"
    brand: str = "YOUR PUBLISHER BRAND"
    imprint: str = "Cosmo Crew Learning Adventures"
    series: str = "Space Adventure"
    world: str = "futuristic space stations, friendly planets, rockets, moons, stars, observatories"
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


def auth(x_builder_key: str | None):
    if not x_builder_key or x_builder_key != BUILDER_KEY:
        raise HTTPException(status_code=401, detail="Invalid X-Builder-Key")


def job_dir(job_id: str) -> Path:
    p = DATA_DIR / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def public_url(request: Request, path: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}{path}"


def draw_star(c, x, y, r):
    import math
    pts = []
    for i in range(10):
        rr = r if i % 2 == 0 else r * 0.42
        a = -math.pi / 2 + i * math.pi / 5
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    p = c.beginPath()
    p.moveTo(*pts[0])
    for pt in pts[1:]:
        p.lineTo(*pt)
    p.close()
    c.drawPath(p, stroke=1, fill=0)


def draw_planet(c, x, y, r):
    c.circle(x, y, r, stroke=1, fill=0)
    c.ellipse(x-r*1.5, y-r*0.33, x+r*1.5, y+r*0.33, stroke=1, fill=0)


def draw_rocket(c, x, y, s):
    p = c.beginPath()
    p.moveTo(x, y+s*1.7)
    p.curveTo(x+s*.8, y+s*1.0, x+s*.8, y-s*.6, x, y-s*1.0)
    p.curveTo(x-s*.8, y-s*.6, x-s*.8, y+s*1.0, x, y+s*1.7)
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    c.circle(x, y+s*.55, s*.25, stroke=1, fill=0)
    c.line(x-s*.55, y-s*.45, x-s*.95, y-s*1.05)
    c.line(x+s*.55, y-s*.45, x+s*.95, y-s*1.05)
    c.line(x-s*.25, y-s*.95, x, y-s*1.5)
    c.line(x+s*.25, y-s*.95, x, y-s*1.5)


def header(c, W, H, title, subtitle=None):
    c.setLineWidth(1.8)
    c.roundRect(0.42*inch, H-1.18*inch, W-0.84*inch, 0.68*inch, 12, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(W/2, H-0.79*inch, title)
    if subtitle:
        c.setFont("Helvetica", 9)
        c.drawCentredString(W/2, H-1.02*inch, subtitle)


def footer(c, W, page_num):
    c.setFont("Helvetica", 7)
    c.drawCentredString(W/2, 0.30*inch, f"{page_num}")


def create_interior(path: Path, payload: dict):
    W = float(payload.get("trim_width", 8.5))*inch
    H = float(payload.get("trim_height", 11))*inch
    page_count = int(payload.get("page_count_target", 28))
    c = canvas.Canvas(str(path), pagesize=(W,H))
    c.setTitle(f"{payload.get('series')} - {payload.get('topic')}")

    # Page 1 Welcome
    header(c, W, H, "WELCOME, SPACE EXPLORER!", f"{payload.get('series')} • {payload.get('topic')} • Ages {payload.get('age_band')}")
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(W/2, H-1.75*inch, "Meet the Cosmo Crew")
    c.setFont("Helvetica", 11)
    c.drawCentredString(W/2, H-2.08*inch, "Color, count, trace, find, match, solve, and discover!")
    # simple diverse crew silhouettes/icons
    xs = [2.2*inch, 4.25*inch, 6.3*inch]
    for i, x in enumerate(xs):
        c.circle(x, H-3.25*inch, 0.34*inch, stroke=1, fill=0)
        c.roundRect(x-0.45*inch, H-5.0*inch, 0.9*inch, 1.35*inch, 18, stroke=1, fill=0)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(x, H-5.28*inch, ["NIA", "MATEO", "ANAYA"][i])
    c.setFont("Helvetica", 10)
    c.drawCentredString(W/2, 1.0*inch, "Use crayons or colored pencils for the cleanest double-sided experience.")
    footer(c,W,1); c.showPage()

    # 24 learning/activity pages (pages 2-25)
    mechanics = [
        ("COLOR + COUNT", "Color exactly {n} stars. Then circle the number {n}."),
        ("TRACE + FIND", "Trace the number {n}. Then find {n} planets."),
        ("MATCH", "Match the number {n} to the group with {n} objects."),
        ("DISCOVER", "Find {n} hidden stars around the rocket."),
        ("COMPARE", "Which group has {n} objects? Circle it, then color it."),
        ("DRAW", "Draw {n} tiny moons around the space station."),
    ]
    for page in range(2, 26):
        n = ((page-2) % 10) + 1
        mech, instruction = mechanics[(page-2) % len(mechanics)]
        header(c, W, H, f"{mech}: NUMBER {n}", "Interactive coloring + learning")
        c.setFont("Helvetica-Bold", 14)
        c.drawString(0.65*inch, H-1.60*inch, instruction.format(n=n))
        c.setFont("Helvetica-Bold", 54)
        c.drawCentredString(W/2, H-2.65*inch, str(n))

        # art zone
        c.setLineWidth(1.5)
        c.roundRect(0.62*inch, 1.0*inch, W-1.24*inch, H-4.05*inch, 16, stroke=1, fill=0)
        if page % 3 == 0:
            draw_rocket(c, W/2, 3.9*inch, 0.55*inch)
        elif page % 3 == 1:
            draw_planet(c, W/2, 4.15*inch, 0.82*inch)
        else:
            c.roundRect(W/2-1.25*inch, 3.35*inch, 2.5*inch, 1.55*inch, 20, stroke=1, fill=0)
            c.circle(W/2, 4.62*inch, 0.28*inch, stroke=1, fill=0)

        # exact n stars across page
        cols = min(n,5)
        for j in range(n):
            row = j // 5
            col = j % 5
            x = 1.35*inch + col*1.45*inch
            y = 2.1*inch + row*0.85*inch
            draw_star(c,x,y,0.22*inch)

        c.setFont("Helvetica", 8)
        c.drawCentredString(W/2, 0.66*inch, "Coloring art is deliberately clean and open for young learners.")
        footer(c,W,page); c.showPage()

    # Page 26 completion
    header(c, W, H, "MISSION COMPLETE!", "You finished the Numbers Adventure")
    c.setFont("Helvetica-Bold", 28)
    c.drawCentredString(W/2, H-2.15*inch, "AMAZING JOB!")
    c.setFont("Helvetica", 14)
    c.drawCentredString(W/2, H-2.65*inch, "This certificate belongs to:")
    c.line(1.35*inch, H-3.25*inch, W-1.35*inch, H-3.25*inch)
    for i in range(10):
        draw_star(c, 1.0*inch + (i%5)*1.62*inch, 2.5*inch + (i//5)*0.95*inch, 0.25*inch)
    c.setFont("Helvetica", 11)
    c.drawCentredString(W/2, 1.25*inch, "You counted, colored, traced, matched, solved, and explored!")
    footer(c,W,26); c.showPage()

    # Page 27 affirmation
    header(c, W, H, "YOUR BIG-BRAIN BOOST", "A little message from the Cosmo Crew")
    affs = payload.get("back_cover_affirmations") or ["Reach for the stars — your imagination can take you anywhere!"]
    affirmation = affs[0]
    c.setFont("Helvetica-Bold", 24)
    words = affirmation.split()
    lines, line = [], []
    for w in words:
        if len(" ".join(line+[w])) > 34:
            lines.append(" ".join(line)); line=[w]
        else:
            line.append(w)
    if line: lines.append(" ".join(line))
    y = H-3.0*inch
    for line in lines:
        c.drawCentredString(W/2, y, line)
        y -= 0.42*inch
    draw_rocket(c, W/2, 3.2*inch, 0.8*inch)
    footer(c,W,27); c.showPage()

    # Page 28 next book teaser
    header(c, W, H, "YOUR NEXT ADVENTURE IS WAITING...", "Keep the learning journey going")
    c.setFont("Helvetica-Bold", 28)
    c.drawCentredString(W/2, H-2.25*inch, "NEXT: THE ALPHABET!")
    c.setFont("Helvetica", 13)
    c.drawCentredString(W/2, H-2.75*inch, "Join the Cosmo Crew for letters A–Z.")
    for idx, ch in enumerate("ABCXYZ"):
        x = 1.2*inch + (idx%3)*3.0*inch
        y = 4.9*inch - (idx//3)*1.8*inch
        c.setFont("Helvetica-Bold", 44)
        c.drawCentredString(x, y, ch)
        draw_star(c,x,y-0.55*inch,0.22*inch)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(W/2, 1.05*inch, "KEEP LEARNING • KEEP EXPLORING • KEEP DREAMING")
    footer(c,W,28); c.showPage()

    c.save()


def create_cover(path: Path, payload: dict):
    page_count = int(payload.get("page_count_target", 28))
    trim_w = float(payload.get("trim_width", 8.5))
    trim_h = float(payload.get("trim_height", 11))
    spine = page_count * 0.002252  # B&W white paper approximation
    bleed = 0.125
    full_w = (trim_w*2 + spine + bleed*2) * inch
    full_h = (trim_h + bleed*2) * inch
    c = canvas.Canvas(str(path), pagesize=(full_w, full_h))
    c.setLineWidth(1.4)

    left = bleed*inch
    back_x = left
    spine_x = (bleed + trim_w)*inch
    front_x = (bleed + trim_w + spine)*inch
    top = full_h - bleed*inch

    # guide rectangles
    c.rect(back_x, bleed*inch, trim_w*inch, trim_h*inch, stroke=1, fill=0)
    c.rect(front_x, bleed*inch, trim_w*inch, trim_h*inch, stroke=1, fill=0)

    # BACK COVER
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(back_x + trim_w*inch/2, top-0.85*inch, "THE ADVENTURE CONTINUES!")
    c.setFont("Helvetica", 12)
    body = [
        "Color your way through a playful space mission",
        "while practicing early number skills.",
        "",
        "Count • Trace • Find • Match • Solve • Discover",
    ]
    y=top-1.35*inch
    for line in body:
        c.drawCentredString(back_x+trim_w*inch/2, y, line)
        y-=0.28*inch
    for j in range(8):
        draw_star(c, back_x+1.15*inch+(j%4)*1.95*inch, 4.0*inch+(j//4)*1.05*inch, 0.24*inch)
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(back_x+trim_w*inch/2, 1.55*inch, "REACH FOR THE STARS!")
    c.setFont("Helvetica", 9)
    c.drawCentredString(back_x+trim_w*inch/2, 1.18*inch, "Your imagination can take you anywhere.")

    # FRONT COVER
    cx = front_x + trim_w*inch/2
    c.setFont("Helvetica-Bold", 28)
    c.drawCentredString(cx, top-0.95*inch, "COSMO CREW")
    c.setFont("Helvetica-Bold", 25)
    c.drawCentredString(cx, top-1.42*inch, "SPACE ADVENTURE")
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(cx, top-1.86*inch, "NUMBERS 1–10")
    c.setFont("Helvetica", 11)
    c.drawCentredString(cx, top-2.18*inch, f"Interactive Coloring + Learning • Ages {payload.get('age_band','3-5')}")
    draw_planet(c, cx, 6.8*inch, 1.15*inch)
    draw_rocket(c, cx, 4.0*inch, 0.85*inch)
    for j in range(10):
        draw_star(c, front_x+0.8*inch+(j%5)*1.7*inch, 2.15*inch+(j//5)*0.95*inch, 0.21*inch)

    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(cx, 0.78*inch, payload.get("imprint","Cosmo Crew Learning Adventures"))

    c.save()


def create_metadata(path: Path, payload: dict, job_id: str):
    metadata = {
        "job_id": job_id,
        "publish_enabled": False,
        "format": "Paperback",
        "trim_size": f"{payload.get('trim_width',8.5)} x {payload.get('trim_height',11)} in",
        "page_count": payload.get("page_count_target",28),
        "interior": "Black & white on white paper",
        "bleed": "No interior bleed in layout preview",
        "cover_finish": "Matte",
        "title": f"Cosmo Crew: {payload.get('series','Space Adventure')} — {payload.get('topic','Numbers 1-10')}",
        "subtitle": f"Interactive Coloring + Learning for Ages {payload.get('age_band','3-5')}",
        "series": payload.get("series"),
        "volume": payload.get("volume",1),
        "next_book": payload.get("next_book",{}),
        "ai_disclosure_required_if_ai_art_is_used": True,
        "preview_mode": PREVIEW_MODE,
        "notes": [
            "TEST ONLY. Nothing is uploaded to KDP.",
            "This first Railway build validates page count, double-sided layout, cover wrap, n8n job polling, and package delivery.",
            "Layout-preview art is programmatic and costs $0. AI illustration generation can be added after layout approval."
        ]
    }
    path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def build_job(job_id: str, payload: dict, base_url: str):
    try:
        jobs[job_id]["status"] = "RUNNING"
        d = job_dir(job_id)
        interior = d / "interior_preview.pdf"
        cover = d / "cover_preview.pdf"
        metadata = d / "metadata.json"
        package = d / "kdp_preview_package.zip"

        create_interior(interior, payload)
        create_cover(cover, payload)
        create_metadata(metadata, payload, job_id)

        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(interior, interior.name)
            z.write(cover, cover.name)
            z.write(metadata, metadata.name)

        jobs[job_id].update({
            "status": "SUCCEEDED",
            "interior_pdf_url": f"{base_url}/files/{job_id}/{interior.name}",
            "cover_pdf_url": f"{base_url}/files/{job_id}/{cover.name}",
            "metadata_url": f"{base_url}/files/{job_id}/{metadata.name}",
            "package_url": f"{base_url}/files/{job_id}/{package.name}",
            "page_count": payload.get("page_count_target", 28),
            "publish_enabled": False,
            "preview_mode": PREVIEW_MODE,
        })
    except Exception as e:
        jobs[job_id].update({"status": "FAILED", "error": repr(e)})


@app.get("/health")
def health():
    return {"ok": True, "service": "kdp-book-builder", "preview_mode": PREVIEW_MODE}


@app.post("/jobs")
def create_job(payload: JobPayload, request: Request, x_builder_key: str | None = Header(default=None)):
    auth(x_builder_key)
    if payload.publish:
        raise HTTPException(status_code=400, detail="Publishing is disabled in TEST builder.")
    job_id = uuid.uuid4().hex[:12]
    base_url = str(request.base_url).rstrip("/")
    jobs[job_id] = {
        "job_id": job_id,
        "status": "QUEUED",
        "created_at": time.time(),
        "status_url": f"{base_url}/status/{job_id}",
        "publish_enabled": False,
    }
    threading.Thread(target=build_job, args=(job_id, payload.model_dump(), base_url), daemon=True).start()
    return jobs[job_id]


@app.get("/status/{job_id}")
def status(job_id: str, x_builder_key: str | None = Header(default=None)):
    auth(x_builder_key)
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]


@app.get("/files/{job_id}/{filename}")
def get_file(job_id: str, filename: str):
    p = (DATA_DIR / job_id / filename).resolve()
    if not str(p).startswith(str(DATA_DIR.resolve())) or not p.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(p), filename=filename)


@app.get("/")
def root():
    return {
        "service": "KDP Coloring Book Builder",
        "status": "ready",
        "test_only": True,
        "routes": ["/health", "POST /jobs", "/status/{job_id}", "/files/{job_id}/{filename}"]
    }
