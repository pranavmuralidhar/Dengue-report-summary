"""
deploy/step3_lambdas.py

Zips backend/lambda_upload and backend/lambda_query and creates (or
updates) the two Lambda functions, wiring in the environment variables
they need (bucket name, Knowledge Base ID, model ARNs, etc).

Requires step1 and step2 to have run first.

Run:
    python step3_lambdas.py
"""

import os
import shutil
import time
import zipfile

import config
import state
from aws_session import get_session

session = get_session()
lambda_client = session.client("lambda")
sts = session.client("sts")
ACCOUNT_ID = sts.get_caller_identity()["Account"]

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
BUILD_DIR = os.path.join(os.path.dirname(__file__), "_build")


def zip_function(function_dir_name):
    src_dir = os.path.join(BACKEND_DIR, function_dir_name)
    os.makedirs(BUILD_DIR, exist_ok=True)
    zip_path = os.path.join(BUILD_DIR, f"{function_dir_name}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for file in files:
                full_path = os.path.join(root, file)
                arcname = os.path.relpath(full_path, src_dir)
                zf.write(full_path, arcname)
    return zip_path


def deploy_function(function_name, zip_path, handler, env_vars, role_arn):
    with open(zip_path, "rb") as f:
        zip_bytes = f.read()

    try:
        lambda_client.get_function(FunctionName=function_name)
        exists = True
    except lambda_client.exceptions.ResourceNotFoundException:
        exists = False

    if exists:
        lambda_client.update_function_code(FunctionName=function_name, ZipFile=zip_bytes)
        # Code updates apply asynchronously - wait before updating config.
        waiter = lambda_client.get_waiter("function_updated")
        waiter.wait(FunctionName=function_name)
        lambda_client.update_function_configuration(
            FunctionName=function_name,
            Handler=handler,
            Environment={"Variables": env_vars},
            Timeout=60,
            MemorySize=256,
        )
        print(f"Updated Lambda function: {function_name}")
    else:
        lambda_client.create_function(
            FunctionName=function_name,
            Runtime="python3.12",
            Role=role_arn,
            Handler=handler,
            Code={"ZipFile": zip_bytes},
            Environment={"Variables": env_vars},
            Timeout=60,
            MemorySize=256,
            Publish=True,
        )
        print(f"Created Lambda function: {function_name}")

    return lambda_client.get_function(FunctionName=function_name)["Configuration"]["FunctionArn"]


if __name__ == "__main__":
    saved = state.load()
    for key in ("lambda_role_arn", "knowledge_base_id", "data_source_id"):
        if key not in saved:
            raise SystemExit(f"Missing '{key}' in state.json - run earlier steps first.")

    lambda_role_arn = saved["lambda_role_arn"]
    kb_id = saved["knowledge_base_id"]
    ds_id = saved["data_source_id"]

    upload_zip = zip_function("lambda_upload")
    query_zip = zip_function("lambda_query")

    # IAM role just created in step1 can take a bit to be assumable by Lambda.
    print("Waiting 10s for IAM role propagation before creating functions...")
    time.sleep(10)

    upload_arn = deploy_function(
        function_name=config.LAMBDA_UPLOAD_FUNCTION_NAME,
        zip_path=upload_zip,
        handler="lambda_function.lambda_handler",
        env_vars={
            "S3_BUCKET_NAME": config.S3_BUCKET_NAME,
            "S3_PREFIX": config.S3_PREFIX,
            "KNOWLEDGE_BASE_ID": kb_id,
            "DATA_SOURCE_ID": ds_id,
        },
        role_arn=lambda_role_arn,
    )

    query_arn = deploy_function(
        function_name=config.LAMBDA_QUERY_FUNCTION_NAME,
        zip_path=query_zip,
        handler="lambda_function.lambda_handler",
        env_vars={
            "KNOWLEDGE_BASE_ID": kb_id,
            "MODEL_ARN": config.GENERATION_MODEL_ARN_TEMPLATE.format(
                region=config.AWS_REGION,
                account_id=ACCOUNT_ID,
            ),
        },
        role_arn=lambda_role_arn,
    )

    shutil.rmtree(BUILD_DIR, ignore_errors=True)

    state.save({"upload_function_arn": upload_arn, "query_function_arn": query_arn})
    print("\nDone.")
    print("  Upload function ARN:", upload_arn)
    print("  Query function ARN: ", query_arn)
