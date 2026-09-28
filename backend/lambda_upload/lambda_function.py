"""
lambda_upload / lambda_function.py

Purpose
-------
Receives a report file (PDF / TXT / DOCX) from the frontend via API Gateway,
stores it in the S3 bucket that backs the Bedrock Knowledge Base, and kicks
off an ingestion job so the Knowledge Base picks up the new document.

Expected input (API Gateway -> Lambda proxy integration, POST /upload)
------------------------------------------------------------------
JSON body:
{
    "file_name": "dengue_report_2026.pdf",
    "file_content_base64": "<base64-encoded file bytes>"
}

Response
--------
{
    "message": "File uploaded and ingestion started",
    "s3_key": "reports/dengue_report_2026.pdf",
    "ingestion_job_id": "..."
}

Environment variables (set on the Lambda in the AWS console or via deploy script)
----------------------------------------------------------------------------
S3_BUCKET_NAME          -> bucket that is registered as the KB data source
S3_PREFIX                -> key prefix to store uploads under (default "reports/")
KNOWLEDGE_BASE_ID        -> Bedrock Knowledge Base ID
DATA_SOURCE_ID           -> Bedrock Knowledge Base Data Source ID
"""

import base64
import json
import os
import re
import uuid

import boto3

s3_client = boto3.client("s3")
bedrock_agent_client = boto3.client("bedrock-agent")

S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]
S3_PREFIX = os.environ.get("S3_PREFIX", "reports/")
KNOWLEDGE_BASE_ID = os.environ["KNOWLEDGE_BASE_ID"]
DATA_SOURCE_ID = os.environ["DATA_SOURCE_ID"]

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "OPTIONS,POST",
}


def _response(status_code, body_dict):
    return {
        "statusCode": status_code,
        "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
        "body": json.dumps(body_dict),
    }


def _safe_file_name(name: str) -> str:
    # Strip path components and keep the request unique so re-uploads
    # of a same-named file don't silently overwrite an in-progress ingest.
    name = os.path.basename(name)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    unique_prefix = uuid.uuid4().hex[:8]
    return f"{unique_prefix}_{name}"


def _clear_previous_reports():
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET_NAME, Prefix=S3_PREFIX):
        objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if objects:
            s3_client.delete_objects(
                Bucket=S3_BUCKET_NAME,
                Delete={"Objects": objects},
            )


def lambda_handler(event, context):
    # Handle CORS preflight
    if event.get("httpMethod") == "OPTIONS":
        return _response(200, {"message": "ok"})

    try:
        body = json.loads(event.get("body") or "{}")
        if body.get("action") == "status":
            ingestion_job_id = body["ingestion_job_id"]
            if ingestion_job_id == "ALREADY_IN_PROGRESS":
                return _response(200, {"status": "IN_PROGRESS"})
            job = bedrock_agent_client.get_ingestion_job(
                knowledgeBaseId=KNOWLEDGE_BASE_ID,
                dataSourceId=DATA_SOURCE_ID,
                ingestionJobId=ingestion_job_id,
            )["ingestionJob"]
            return _response(
                200,
                {
                    "status": job.get("status", "IN_PROGRESS"),
                    "failure_reasons": job.get("failureReasons", []),
                },
            )
        file_name = body["file_name"]
        file_content_base64 = body["file_content_base64"]
    except (KeyError, json.JSONDecodeError) as exc:
        return _response(400, {"error": f"Invalid request body: {exc}"})

    try:
        file_bytes = base64.b64decode(file_content_base64)
    except Exception as exc:  # noqa: BLE001
        return _response(400, {"error": f"Could not decode file_content_base64: {exc}"})

    # Basic size guard (Bedrock KB / Lambda payload practicality) - 20 MB
    if len(file_bytes) > 20 * 1024 * 1024:
        return _response(400, {"error": "File too large. Max 20 MB via this endpoint."})

    safe_name = _safe_file_name(file_name)
    s3_key = f"{S3_PREFIX}{safe_name}"

    try:
        _clear_previous_reports()
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=file_bytes,
        )
    except Exception as exc:  # noqa: BLE001
        return _response(500, {"error": f"S3 upload failed: {exc}"})

    # Kick off (or re-use) a Knowledge Base ingestion job so the new file
    # gets chunked + embedded and becomes searchable.
    try:
        ingestion_response = bedrock_agent_client.start_ingestion_job(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            dataSourceId=DATA_SOURCE_ID,
            description=f"Ingest {safe_name} uploaded via web app",
        )
        ingestion_job_id = ingestion_response["ingestionJob"]["ingestionJobId"]
    except bedrock_agent_client.exceptions.ConflictException:
        # An ingestion job is already running for this data source.
        # The new file will be picked up by that job (or the next one);
        # this is not an error from the caller's point of view.
        ingestion_job_id = "ALREADY_IN_PROGRESS"
    except Exception as exc:  # noqa: BLE001
        return _response(
            200,
            {
                "message": "File uploaded, but starting ingestion failed. "
                "It will be picked up on the next sync.",
                "s3_key": s3_key,
                "ingestion_error": str(exc),
            },
        )

    return _response(
        200,
        {
            "message": "File uploaded and ingestion started",
            "s3_key": s3_key,
            "ingestion_job_id": ingestion_job_id,
        },
    )
