"""
deploy/teardown.py

Deletes everything the deploy scripts created, in reverse order, so you
don't keep paying for the OpenSearch Serverless collection / Lambdas /
API Gateway if you're just experimenting.

NOTE: This does NOT delete the S3 bucket contents or the bucket itself
by default (uploaded reports might be worth keeping). Pass --delete-bucket
to also empty and delete the bucket.

Run:
    python teardown.py
    python teardown.py --delete-bucket
"""

import sys

import config
import state
from aws_session import get_session

session = get_session()
apigw = session.client("apigateway")
lambda_client = session.client("lambda")
bedrock_agent = session.client("bedrock-agent")
aoss = session.client("opensearchserverless")
iam = session.client("iam")
s3 = session.client("s3")

saved = state.load()


def delete_api_gateway():
    if "api_id" in saved:
        try:
            apigw.delete_rest_api(restApiId=saved["api_id"])
            print("Deleted API Gateway")
        except Exception as exc:  # noqa: BLE001
            print(f"  skip API Gateway: {exc}")


def delete_lambdas():
    for name in (config.LAMBDA_UPLOAD_FUNCTION_NAME, config.LAMBDA_QUERY_FUNCTION_NAME):
        try:
            lambda_client.delete_function(FunctionName=name)
            print(f"Deleted Lambda: {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"  skip Lambda {name}: {exc}")


def delete_kb():
    kb_id = saved.get("knowledge_base_id")
    ds_id = saved.get("data_source_id")
    if kb_id and ds_id:
        try:
            data_source = bedrock_agent.get_data_source(
                knowledgeBaseId=kb_id,
                dataSourceId=ds_id,
            )["dataSource"]
            bedrock_agent.update_data_source(
                knowledgeBaseId=kb_id,
                dataSourceId=ds_id,
                name=data_source["name"],
                dataSourceConfiguration=data_source["dataSourceConfiguration"],
                vectorIngestionConfiguration=data_source["vectorIngestionConfiguration"],
                dataDeletionPolicy="RETAIN",
            )
            bedrock_agent.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id)
            print("Deleted data source")
        except Exception as exc:  # noqa: BLE001
            print(f"  skip data source: {exc}")
    if kb_id:
        try:
            bedrock_agent.delete_knowledge_base(knowledgeBaseId=kb_id)
            print("Deleted Knowledge Base")
        except Exception as exc:  # noqa: BLE001
            print(f"  skip Knowledge Base: {exc}")


def delete_opensearch():
    try:
        aoss.delete_collection(
            id=aoss.batch_get_collection(names=[config.OPENSEARCH_COLLECTION_NAME])[
                "collectionDetails"
            ][0]["id"]
        )
        print("Deleted OpenSearch Serverless collection")
    except Exception as exc:  # noqa: BLE001
        print(f"  skip collection: {exc}")

    for name, ptype in (
        (f"{config.OPENSEARCH_COLLECTION_NAME}-enc", "encryption"),
        (f"{config.OPENSEARCH_COLLECTION_NAME}-net", "network"),
    ):
        try:
            aoss.delete_security_policy(name=name, type=ptype)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip policy {name}: {exc}")
    try:
        aoss.delete_access_policy(name=f"{config.OPENSEARCH_COLLECTION_NAME}-access", type="data")
    except Exception as exc:  # noqa: BLE001
        print(f"  skip access policy: {exc}")


def delete_iam_roles():
    for role_name in (config.IAM_KB_ROLE_NAME, config.IAM_LAMBDA_ROLE_NAME):
        try:
            policies = iam.list_role_policies(RoleName=role_name)["PolicyNames"]
            for p in policies:
                iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            iam.delete_role(RoleName=role_name)
            print(f"Deleted IAM role: {role_name}")
        except Exception as exc:  # noqa: BLE001
            print(f"  skip role {role_name}: {exc}")


def delete_bucket():
    try:
        paginator = s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=config.S3_BUCKET_NAME):
            objects = [
                {"Key": v["Key"], "VersionId": v["VersionId"]}
                for v in page.get("Versions", []) + page.get("DeleteMarkers", [])
            ]
            if objects:
                s3.delete_objects(Bucket=config.S3_BUCKET_NAME, Delete={"Objects": objects})
        s3.delete_bucket(Bucket=config.S3_BUCKET_NAME)
        print("Emptied and deleted S3 bucket")
    except Exception as exc:  # noqa: BLE001
        print(f"  skip bucket: {exc}")


if __name__ == "__main__":
    delete_api_gateway()
    delete_lambdas()
    delete_kb()
    delete_opensearch()
    delete_iam_roles()
    if "--delete-bucket" in sys.argv:
        delete_bucket()
    else:
        print("\nS3 bucket left in place (pass --delete-bucket to remove it too).")
    print("\nTeardown complete.")
