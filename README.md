# KDP Coloring Book Builder — TEST ONLY

This is the Railway service for the n8n workflow:
`KDP Coloring Book Factory v4 - TEST ONLY - No Env`.

## What this first version does

- Receives one test-book specification from n8n.
- Creates a 28-page 8.5x11 interior preview PDF.
- Creates a full-wrap paperback cover preview PDF.
- Creates metadata.json.
- Returns downloadable URLs to n8n.
- Does NOT publish to KDP.
- Does NOT use holiday/seasonal content.
- Does NOT use paid image generation in the first layout test.

## Railway Variables

Set:

BUILDER_KEY=<your secret>
PREVIEW_MODE=layout

## API

GET /health
POST /jobs
GET /status/{job_id}
GET /files/{job_id}/{filename}

POST /jobs requires header:
X-Builder-Key: <BUILDER_KEY>
