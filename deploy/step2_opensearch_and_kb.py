"""
deploy/step2_opensearch_and_kb.py

Creates:
  1. An OpenSearch Serverless collection (type VECTORSEARCH) to store report
     embeddings, with the encryption / network / data-access policies it needs.
  2. A vector index inside that collection.
  3. A Bedrock Knowledge Base pointing at that index, using Titan Text
     Embeddings v2.
  4. An S3 data source on the Knowledge Base pointing at the report bucket.

Requires step1 to have run first (uses the KB IAM role it created).

Extra dependency for this step only:
    pip install opensearch-py requests-aws4auth

Run:
    python step2_opensearch_and_kb.py

This step involves several "wait until ACTIVE" polling loops because AWS
provisions the OpenSearch Serverless collection asynchronously - the whole
script can take 5-10 minutes.
"""

import json
import time

import config
import state
from aws_session import get_session

session = get_session()
aoss = session.client("opensearchserverless")
bedrock_agent = session.client("bedrock-agent")
sts = session.client("sts")

ACCOUNT_ID = sts.get_caller_identity()["Account"]
COLLECTION_NAME = config.OPENSEARCH_COLLECTION_NAME
INDEX_NAME = config.OPENSEARCH_INDEX_NAME
VECTOR_FIELD = "bedrock-knowledge-base-default-vector"


def create_encryption_policy():
    try:
        aoss.create_security_policy(
            name=f"{COLLECTION_NAME}-enc",
            type="encryption",
            policy=json.dumps(
                {
                    "Rules": [
                        {
                            "ResourceType": "collection",
                            "Resource": [f"collection/{COLLECTION_NAME}"],
                        }
                    ],
                    "AWSOwnedKey": True,
                }
            ),
        )
        print("Created encryption policy")
    except aoss.exceptions.ConflictException:
        print("Encryption policy already exists")


def create_network_policy():
    try:
        aoss.create_security_policy(
            name=f"{COLLECTION_NAME}-net",
            type="network",
            policy=json.dumps(
                [
                    {
                        "Rules": [
                            {
                                "ResourceType": "collection",
                                "Resource": [f"collection/{COLLECTION_NAME}"],
                            },
                            {
                                "ResourceType": "dashboard",
                                "Resource": [f"collection/{COLLECTION_NAME}"],
                            },
                        ],
                        "AllowFromPublic": True,
                    }
                ]
            ),
        )
        print("Created network policy")
    except aoss.exceptions.ConflictException:
        print("Network policy already exists")


def create_data_access_policy(kb_role_arn, caller_arn):
    try:
        aoss.create_access_policy(
            name=f"{COLLECTION_NAME}-access",
            type="data",
            policy=json.dumps(
                [
                    {
                        "Rules": [
                            {
                                "ResourceType": "collection",
                                "Resource": [f"collection/{COLLECTION_NAME}"],
                                "Permission": [
                                    "aoss:CreateCollectionItems",
                                    "aoss:DeleteCollectionItems",
                                    "aoss:UpdateCollectionItems",
                                    "aoss:DescribeCollectionItems",
                                ],
                            },
                            {
                                "ResourceType": "index",
                                "Resource": [f"index/{COLLECTION_NAME}/*"],
                                "Permission": [
                                    "aoss:CreateIndex",
                                    "aoss:DeleteIndex",
                                    "aoss:UpdateIndex",
                                    "aoss:DescribeIndex",
                                    "aoss:ReadDocument",
                                    "aoss:WriteDocument",
                                ],
                            },
                        ],
                        # Both the Bedrock KB role AND the identity running this
                        # script need access - the latter so this script can
                        # create the vector index over the HTTPS data API.
                        "Principal": [kb_role_arn, caller_arn],
                    }
                ]
            ),
        )
        print("Created data access policy")
    except aoss.exceptions.ConflictException:
        print("Data access policy already exists")


def create_collection():
    try:
        resp = aoss.create_collection(name=COLLECTION_NAME, type="VECTORSEARCH")
        print("Creating OpenSearch Serverless collection (this takes a few minutes)...")
    except aoss.exceptions.ConflictException:
        pass

    while True:
        resp = aoss.batch_get_collection(names=[COLLECTION_NAME])
        details = resp["collectionDetails"]
        if details and details[0]["status"] == "ACTIVE":
            collection = details[0]
            print(f"Collection ACTIVE: {collection['arn']}")
            return collection
        print("  ...still creating, checking again in 20s")
        time.sleep(20)


def create_vector_index(collection_endpoint):
    from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

    credentials = session.get_credentials()
    auth = AWSV4SignerAuth(credentials, config.AWS_REGION, "aoss")

    client = OpenSearch(
        hosts=[{"host": collection_endpoint.replace("https://", ""), "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=60,
    )

    if client.indices.exists(index=INDEX_NAME):
        print(f"Vector index '{INDEX_NAME}' already exists")
        return

    index_body = {
        "settings": {"index.knn": True},
        "mappings": {
            "properties": {
                VECTOR_FIELD: {
                    "type": "knn_vector",
                    "dimension": 1024,  # Titan Text Embeddings v2 default dim
                    "method": {
                        "engine": "faiss",
                        "name": "hnsw",
                        "space_type": "l2",
                    },
                },
                "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},
                "AMAZON_BEDROCK_METADATA": {"type": "text", "index": False},
            }
        },
    }

    # Retry loop: right after the data-access policy is created it can take
    # a short while before OpenSearch actually honours it.
    for attempt in range(10):
        try:
            client.indices.create(index=INDEX_NAME, body=index_body)
            print(f"Created vector index: {INDEX_NAME}")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"  index create attempt {attempt + 1} failed: {exc}; retrying in 15s")
            time.sleep(15)
    raise SystemExit("Could not create the OpenSearch vector index after multiple attempts.")


def create_knowledge_base(kb_role_arn, collection_arn):
    embedding_model_arn = config.EMBEDDING_MODEL_ARN_TEMPLATE.format(region=config.AWS_REGION)

    existing = bedrock_agent.list_knowledge_bases().get("knowledgeBaseSummaries", [])
    for kb in existing:
        if kb["name"] == config.KNOWLEDGE_BASE_NAME:
            if kb.get("status") == "DELETE_UNSUCCESSFUL":
                raise SystemExit(
                    f"Knowledge Base {kb['knowledgeBaseId']} is DELETE_UNSUCCESSFUL. "
                    "Run teardown.py successfully before redeploying."
                )
            print(f"Knowledge Base already exists: {kb['knowledgeBaseId']}")
            return kb["knowledgeBaseId"]

    resp = bedrock_agent.create_knowledge_base(
        name=config.KNOWLEDGE_BASE_NAME,
        description="Knowledge base over uploaded dengue reports",
        roleArn=kb_role_arn,
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {"embeddingModelArn": embedding_model_arn},
        },
        storageConfiguration={
            "type": "OPENSEARCH_SERVERLESS",
            "opensearchServerlessConfiguration": {
                "collectionArn": collection_arn,
                "vectorIndexName": INDEX_NAME,
                "fieldMapping": {
                    "vectorField": VECTOR_FIELD,
                    "textField": "AMAZON_BEDROCK_TEXT_CHUNK",
                    "metadataField": "AMAZON_BEDROCK_METADATA",
                },
            },
        },
    )
    kb_id = resp["knowledgeBase"]["knowledgeBaseId"]
    print(f"Created Knowledge Base: {kb_id}")
    return kb_id


def create_data_source(kb_id):
    existing = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id).get(
        "dataSourceSummaries", []
    )
    for ds in existing:
        if ds["name"] == config.DATA_SOURCE_NAME:
            print(f"Data source already exists: {ds['dataSourceId']}")
            return ds["dataSourceId"]

    resp = bedrock_agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=config.DATA_SOURCE_NAME,
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {
                "bucketArn": f"arn:aws:s3:::{config.S3_BUCKET_NAME}",
                "inclusionPrefixes": [config.S3_PREFIX],
            },
        },
        vectorIngestionConfiguration={
            "chunkingConfiguration": {
                "chunkingStrategy": "FIXED_SIZE",
                "fixedSizeChunkingConfiguration": {
                    "maxTokens": 400,
                    "overlapPercentage": 20,
                },
            }
        },
    )
    ds_id = resp["dataSource"]["dataSourceId"]
    print(f"Created data source: {ds_id}")
    return ds_id


if __name__ == "__main__":
    saved = state.load()
    kb_role_arn = saved.get("kb_role_arn")
    if not kb_role_arn:
        raise SystemExit("Run step1_s3_and_roles.py first (kb_role_arn missing from state.json).")

    caller_arn = sts.get_caller_identity()["Arn"]

    create_encryption_policy()
    create_network_policy()
    create_data_access_policy(kb_role_arn, caller_arn)
    collection = create_collection()

    print("Waiting 30s for the data access policy to propagate before creating the index...")
    time.sleep(30)
    create_vector_index(collection["collectionEndpoint"])

    kb_id = create_knowledge_base(kb_role_arn, collection["arn"])
    ds_id = create_data_source(kb_id)

    state.save(
        {
            "collection_arn": collection["arn"],
            "collection_endpoint": collection["collectionEndpoint"],
            "knowledge_base_id": kb_id,
            "data_source_id": ds_id,
        }
    )
    print("\nDone. Knowledge Base ID:", kb_id, " Data Source ID:", ds_id)
