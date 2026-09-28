"""
lambda_query / lambda_function.py

Purpose
-------
Receives a question from the frontend, asks the Bedrock Knowledge Base to
retrieve the most relevant chunks from the uploaded report(s) and generate
an answer strictly grounded in those chunks, then returns the answer PLUS
the evidence (source chunks + S3 locations) used to produce it.

Expected input (API Gateway -> Lambda proxy integration, POST /ask)
------------------------------------------------------------------
JSON body:
{
    "question": "What was the peak number of dengue cases reported in July?",
    "session_id": "optional - pass back the one returned by a previous call
                    to keep a multi-turn conversation with the Knowledge Base"
}

Response
--------
{
    "answer": "...",
    "session_id": "...",
    "evidence": [
        {
            "text": "<the exact chunk of the report used as evidence>",
            "s3_uri": "s3://bucket/reports/xyz.pdf",
            "score": 0.83
        },
        ...
    ]
}

Environment variables
----------------------
KNOWLEDGE_BASE_ID        -> Bedrock Knowledge Base ID
MODEL_ARN                -> Foundation model ARN used to generate the answer
                             e.g. arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0
"""

import json
import os

import boto3

bedrock_agent_runtime_client = boto3.client("bedrock-agent-runtime")

KNOWLEDGE_BASE_ID = os.environ["KNOWLEDGE_BASE_ID"]
MODEL_ARN = os.environ["MODEL_ARN"]

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "OPTIONS,POST",
}

# System-level instruction injected into the generation step so answers
# stay grounded in the uploaded report and clearly say so when the report
# doesn't cover something.
ANSWER_PROMPT_TEMPLATE = """
You are answering questions about a dengue surveillance/report document that
has been uploaded by the user. Use ONLY the information in the search
results below to answer the question. If the search results do not contain
the answer, say clearly that the report does not cover that, instead of
guessing.

Cite the specific figures, dates, or statements from the report that support
your answer.

Search results:
$search_results$

Question: $query$

Answer, grounded strictly in the search results above:
"""


def _response(status_code, body_dict):
    return {
        "statusCode": status_code,
        "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
        "body": json.dumps(body_dict),
    }


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return _response(200, {"message": "ok"})

    try:
        body = json.loads(event.get("body") or "{}")
        question = body["question"]
    except (KeyError, json.JSONDecodeError) as exc:
        return _response(400, {"error": f"Invalid request body: {exc}"})

    session_id = body.get("session_id")

    request_kwargs = {
        "input": {"text": question},
        "retrieveAndGenerateConfiguration": {
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": KNOWLEDGE_BASE_ID,
                "modelArn": MODEL_ARN,
                "generationConfiguration": {
                    "promptTemplate": {"textPromptTemplate": ANSWER_PROMPT_TEMPLATE}
                },
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {"numberOfResults": 6}
                },
            },
        },
    }
    if session_id:
        request_kwargs["sessionId"] = session_id

    try:
        result = bedrock_agent_runtime_client.retrieve_and_generate(**request_kwargs)
    except Exception as exc:  # noqa: BLE001
        return _response(500, {"error": f"Bedrock query failed: {exc}"})

    answer_text = result.get("output", {}).get("text", "")
    new_session_id = result.get("sessionId", session_id)

    evidence = []
    for citation in result.get("citations", []):
        for ref in citation.get("retrievedReferences", []):
            chunk_text = ref.get("content", {}).get("text", "")
            s3_uri = ref.get("location", {}).get("s3Location", {}).get("uri", "")
            evidence.append(
                {
                    "text": chunk_text,
                    "s3_uri": s3_uri,
                }
            )

    return _response(
        200,
        {
            "answer": answer_text,
            "session_id": new_session_id,
            "evidence": evidence,
        },
    )
