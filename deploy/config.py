"""
deploy/config.py

Edit the values below before running any deploy script.
Every other script in /deploy imports from this file, so this is the
single place you need to touch to point the project at your own AWS
account.
"""

# ---- AWS basics -----------------------------------------------------------
AWS_REGION = "ap-southeast-2"          # Bedrock Knowledge Bases are only in some
                                   # regions - check current availability at
                                   # https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-supported.html
AWS_PROFILE = None                # e.g. "default" or a named profile from
                                   # `aws configure --profile <name>`. Leave
                                   # None to use default credential chain
                                   # (env vars / instance role / default profile).

# ---- Naming (must be globally-unique for S3) -------------------------------
PROJECT_NAME = "dengue-report-qa"          # used as a prefix for resource names
S3_BUCKET_NAME = "dengue-report-qa-docs-pranav-2026"  # <-- must be globally unique
S3_PREFIX = "reports/"

OPENSEARCH_COLLECTION_NAME = "dengue-qa-vectors"
OPENSEARCH_INDEX_NAME = "dengue-qa-index"

KNOWLEDGE_BASE_NAME = "dengue-report-kb"
DATA_SOURCE_NAME = "dengue-report-s3-source"

EMBEDDING_MODEL_ARN_TEMPLATE = (
    "arn:aws:bedrock:{region}::foundation-model/amazon.titan-embed-text-v2:0"
)
# Generation model used to answer questions. Change to any Claude model
# you have Bedrock model access enabled for.
GENERATION_MODEL_ARN_TEMPLATE = (
    "arn:aws:bedrock:{region}:{account_id}:inference-profile/apac.amazon.nova-lite-v1:0"
)

LAMBDA_UPLOAD_FUNCTION_NAME = f"{PROJECT_NAME}-upload"
LAMBDA_QUERY_FUNCTION_NAME = f"{PROJECT_NAME}-query"

IAM_LAMBDA_ROLE_NAME = f"{PROJECT_NAME}-lambda-role"
IAM_KB_ROLE_NAME = f"{PROJECT_NAME}-kb-role"

API_GATEWAY_NAME = f"{PROJECT_NAME}-api"
API_STAGE_NAME = "prod"
