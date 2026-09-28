"""
deploy/step1_s3_and_roles.py

Creates:
  1. The S3 bucket that stores uploaded reports and backs the Bedrock
     Knowledge Base data source.
  2. An IAM role for the Bedrock Knowledge Base (lets Bedrock read the S3
     bucket and call the embedding model / OpenSearch Serverless collection).
  3. An IAM role for the two Lambda functions (lets them write to S3, call
     Bedrock, and write CloudWatch logs).

Run:
    python step1_s3_and_roles.py

Safe to re-run: it checks whether each resource already exists first.
"""

import json
import time

import config
from aws_session import get_session

session = get_session()
s3 = session.client("s3")
iam = session.client("iam")
sts = session.client("sts")

ACCOUNT_ID = sts.get_caller_identity()["Account"]


def create_bucket():
    try:
        if config.AWS_REGION == "us-east-1":
            s3.create_bucket(Bucket=config.S3_BUCKET_NAME)
        else:
            s3.create_bucket(
                Bucket=config.S3_BUCKET_NAME,
                CreateBucketConfiguration={"LocationConstraint": config.AWS_REGION},
            )
        print(f"Created S3 bucket: {config.S3_BUCKET_NAME}")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"S3 bucket already exists (owned by you): {config.S3_BUCKET_NAME}")
    except s3.exceptions.BucketAlreadyExists:
        raise SystemExit(
            f"Bucket name '{config.S3_BUCKET_NAME}' is taken globally. "
            "Edit S3_BUCKET_NAME in config.py to something unique and re-run."
        )

    s3.put_bucket_versioning(
        Bucket=config.S3_BUCKET_NAME,
        VersioningConfiguration={"Status": "Enabled"},
    )
    s3.put_public_access_block(
        Bucket=config.S3_BUCKET_NAME,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )


def create_kb_role():
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": ACCOUNT_ID},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock:{config.AWS_REGION}:{ACCOUNT_ID}:knowledge-base/*"
                    },
                },
            }
        ],
    }

    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{config.S3_BUCKET_NAME}",
                    f"arn:aws:s3:::{config.S3_BUCKET_NAME}/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": [
                    config.EMBEDDING_MODEL_ARN_TEMPLATE.format(region=config.AWS_REGION)
                ],
            },
            {
                "Effect": "Allow",
                "Action": ["aoss:APIAccessAll"],
                "Resource": "*",
            },
        ],
    }

    role_name = config.IAM_KB_ROLE_NAME
    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Role assumed by Bedrock Knowledge Base for the dengue report QA project",
        )
        role_arn = role["Role"]["Arn"]
        print(f"Created IAM role: {role_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        print(f"IAM role already exists: {role_name}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{role_name}-permissions",
        PolicyDocument=json.dumps(permissions_policy),
    )
    return role_arn


def create_lambda_role():
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": "arn:aws:logs:*:*:*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": f"arn:aws:s3:::{config.S3_BUCKET_NAME}",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
                "Resource": f"arn:aws:s3:::{config.S3_BUCKET_NAME}/*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:StartIngestionJob",
                    "bedrock:GetIngestionJob",
                    "bedrock:Retrieve",
                    "bedrock:RetrieveAndGenerate",
                    "bedrock:InvokeModel",
                    "bedrock:GetInferenceProfile",
                ],
                "Resource": "*",
            },
        ],
    }

    role_name = config.IAM_LAMBDA_ROLE_NAME
    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Role used by the upload/query Lambdas for the dengue report QA project",
        )
        role_arn = role["Role"]["Arn"]
        print(f"Created IAM role: {role_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        print(f"IAM role already exists: {role_name}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{role_name}-permissions",
        PolicyDocument=json.dumps(permissions_policy),
    )
    return role_arn


if __name__ == "__main__":
    create_bucket()
    kb_role_arn = create_kb_role()
    lambda_role_arn = create_lambda_role()

    # IAM role propagation can take a few seconds before other services
    # (Bedrock, Lambda) can reliably assume the role.
    print("Waiting 10s for IAM role propagation...")
    time.sleep(10)

    print("\nDone. Save these ARNs (also written to deploy/state.json):")
    print(f"  KB role ARN:     {kb_role_arn}")
    print(f"  Lambda role ARN: {lambda_role_arn}")

    import state

    state.save({"kb_role_arn": kb_role_arn, "lambda_role_arn": lambda_role_arn})
