# Dengue Report Q&A

Upload a dengue surveillance report (PDF/DOCX/TXT/CSV), then ask questions
about it in a chat UI. Every answer is generated strictly from the report's
contents and is shown alongside the exact report excerpts ("evidence") that
were used to produce it.

## How it works

```
Browser (frontend/)
   │  1. upload file (base64 over HTTPS)
   ▼
API Gateway  ──POST /upload──▶  Lambda "upload"  ──▶  S3 bucket (reports/)
                                        │
                                        └─▶ starts a Bedrock Knowledge Base
                                            ingestion job on the S3 data source

API Gateway  ──POST /ask─────▶  Lambda "query"   ──▶  Bedrock Knowledge Base
                                                          (RetrieveAndGenerate)
                                                             │
                                          ┌──────────────────┴───────────────────┐
                                          ▼                                      ▼
                              Titan Embedding model              OpenSearch Serverless
                              (turns the question into a vector)  (vector search over
                                                                    the report's chunks)
                                          │
                                          ▼
                              Claude (generation model) writes the answer,
                              grounded only in the retrieved chunks
```

- **S3** stores the uploaded report files and is registered as the Knowledge
  Base's data source.
- **Bedrock Knowledge Base** chunks each report, embeds the chunks with the
  **Titan Text Embeddings v2** model, and stores the vectors in an
  **OpenSearch Serverless** vector collection.
- When you ask a question, the query Lambda calls Bedrock's
  `RetrieveAndGenerate` API, which retrieves the most relevant chunks and
  asks a Claude model to answer **using only those chunks**. The chunks
  (with their source file) are returned to the frontend as "evidence".
- **API Gateway** exposes two REST endpoints (`/upload`, `/ask`) backed by
  two **Lambda** functions, all wired up with **boto3**.

## Repository layout

```
dengue-qa/
├── backend/
│   ├── lambda_upload/lambda_function.py   # POST /upload
│   └── lambda_query/lambda_function.py    # POST /ask
├── deploy/
│   ├── config.py                # <-- edit this first
│   ├── aws_session.py
│   ├── state.py                 # tracks created resource IDs between steps
│   ├── step1_s3_and_roles.py
│   ├── step2_opensearch_and_kb.py
│   ├── step3_lambdas.py
│   ├── step4_api_gateway.py
│   ├── deploy_all.py            # runs steps 1-4 in order
│   ├── teardown.py              # deletes everything again
│   └── requirements.txt
└── frontend/
    ├── index.html
    ├── style.css
    └── app.js                   # <-- paste your API URL here after deploying
```

---

## 1. Prerequisites

- An AWS account with permission to create S3 buckets, IAM roles, Lambda
  functions, API Gateway REST APIs, OpenSearch Serverless collections, and
  Bedrock Knowledge Bases.
- **Model access enabled in Bedrock** for:
  - `amazon.titan-embed-text-v2:0` (embeddings)
  - `anthropic.claude-3-5-sonnet-20241022-v2:0` (or another Claude model you
    have access to, for generating answers)
  Enable these once in the AWS Console under **Bedrock → Model access** in
  the region you plan to deploy to.
- Python 3.10+ installed locally.
- The AWS CLI installed (`pip install awscli` or via your OS package
  manager) so you can run `aws configure`.

## 2. Configure your AWS credentials

Run:

```bash
aws configure
```

and enter your Access Key ID, Secret Access Key, and default region (e.g.
`us-east-1`). This writes credentials to `~/.aws/credentials`, which boto3
reads automatically — none of the deploy scripts take a key directly.

If you use a named profile instead (`aws configure --profile dengue-qa`),
set `AWS_PROFILE = "dengue-qa"` in `deploy/config.py`.

> Check that Bedrock Knowledge Bases and OpenSearch Serverless are both
> available in the region you choose — not every AWS region supports them
> yet.

## 3. Install dependencies

```bash
cd dengue-qa/deploy
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Edit `deploy/config.py`

At minimum change:

- `AWS_REGION` — must support Bedrock Knowledge Bases + OpenSearch Serverless.
- `S3_BUCKET_NAME` — S3 bucket names are globally unique across ALL AWS
  accounts, so `dengue-report-qa-docs-CHANGE-ME` will fail. Pick something
  unique, e.g. `dengue-report-qa-docs-yourname-2026`.
- `AWS_PROFILE` — if you're using a named CLI profile.

Everything else has a sensible default and can be left as-is.

## 5. Deploy

Run the steps one at a time from inside `deploy/` (recommended the first
time, so you can see each stage succeed before moving on):

```bash
python step1_s3_and_roles.py       # S3 bucket + IAM roles (~15s)
python step2_opensearch_and_kb.py  # OpenSearch collection + Bedrock KB (~5-10 min)
python step3_lambdas.py            # zips & deploys the two Lambdas (~30s)
python step4_api_gateway.py        # REST API + CORS (~15s)
```

Or run all four in sequence:

```bash
python deploy_all.py
```

Each script is safe to re-run — it checks whether its resources already
exist before creating them. Progress/IDs are saved to `deploy/state.json`
so later steps can find what earlier steps created.

**Step 2 is the slowest and most failure-prone step** — OpenSearch
Serverless collections take several minutes to become `ACTIVE`, and the
vector index creation can need a retry or two while the data-access policy
propagates. If it fails partway, just re-run `python step2_opensearch_and_kb.py`
— it picks up where it left off.

When `step4_api_gateway.py` finishes, it prints something like:

```
Done. API base URL:
  https://abc123xyz.execute-api.us-east-1.amazonaws.com/prod
  Upload endpoint: https://abc123xyz.execute-api.us-east-1.amazonaws.com/prod/upload
  Ask endpoint:    https://abc123xyz.execute-api.us-east-1.amazonaws.com/prod/ask
```

## 6. Point the frontend at your API

Open `frontend/app.js` and replace:

```js
const API_BASE_URL = "https://REPLACE_ME.execute-api.REGION.amazonaws.com/prod";
```

with the base URL printed in the previous step.

## 7. Run the frontend

No build step needed — it's plain HTML/CSS/JS. Easiest way to serve it
locally:

```bash
cd dengue-qa/frontend
python -m http.server 8080
```

Then open `http://localhost:8080` in your browser. (Opening `index.html`
directly with `file://` also works for this simple app, but a local server
avoids any browser quirks with `fetch`.)

## 8. Use it

1. Click the upload area, choose a report file, click **Upload report**.
2. Wait roughly 1-2 minutes for Bedrock to finish chunking/embedding it
   (there's no push notification for this in the current UI — if your first
   question comes back saying the report doesn't cover something it clearly
   does, just wait a bit longer and ask again).
3. Ask a question in the chat box. Each answer has an **"Evidence from the
   report"** dropdown showing the exact chunks of text that were used to
   generate it, and which file they came from.

You can keep asking follow-up questions — `session_id` is carried between
requests so Bedrock keeps conversational context.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `step2_opensearch_and_kb.py` fails creating the vector index | Data-access policy hasn't propagated yet — wait a minute and re-run the script. |
| `AccessDeniedException` calling Bedrock | Model access isn't enabled for the embedding/generation model in that region — check **Bedrock → Model access** in the console. |
| Upload succeeds but answers say "the report does not cover that" for something it clearly does | Ingestion hasn't finished yet. Check **Bedrock console → Knowledge Bases → your KB → Data source → Sync history**, or just wait ~1-2 minutes and re-ask. |
| CORS error in the browser console | Re-run `step4_api_gateway.py` — it (re)creates the CORS `OPTIONS` methods and redeploys the stage. |
| `BucketAlreadyExists` in step 1 | Someone else already owns that bucket name globally — change `S3_BUCKET_NAME` in `config.py`. |

## Tearing it down

To avoid ongoing charges (OpenSearch Serverless in particular bills
per-OCU-hour even when idle):

```bash
cd deploy
python teardown.py                # deletes API Gateway, Lambdas, KB, OpenSearch collection, IAM roles
python teardown.py --delete-bucket # also empties and deletes the S3 bucket
```

## Cost notes

- OpenSearch Serverless has a minimum billing footprint even at rest — it's
  the main ongoing cost of leaving this deployed. Tear it down when not in
  active use if cost is a concern.
- Bedrock charges per embedding call (small, at ingestion time) and per
  token for the generation model (at question time).
- S3, Lambda and API Gateway costs for this workload are negligible.
