"""
deploy/step4_api_gateway.py

Creates a REST API (API Gateway v1) with:
  POST /upload  -> lambda_upload
  POST /ask     -> lambda_query
CORS-enabled (OPTIONS mock methods) so the static frontend can call it
directly from the browser.

Requires step3 to have run first.

Run:
    python step4_api_gateway.py

At the end it prints the invoke URL - paste it into frontend/app.js
(API_BASE_URL) before opening the frontend.
"""

import json

import config
import state
from aws_session import get_session

session = get_session()
apigw = session.client("apigateway")
lambda_client = session.client("lambda")
sts = session.client("sts")

ACCOUNT_ID = sts.get_caller_identity()["Account"]


def get_or_create_rest_api():
    apis = apigw.get_rest_apis(limit=500)["items"]
    for api in apis:
        if api["name"] == config.API_GATEWAY_NAME:
            print(f"REST API already exists: {api['id']}")
            return api["id"]
    api = apigw.create_rest_api(
        name=config.API_GATEWAY_NAME,
        description="API for the dengue report QA project",
        endpointConfiguration={"types": ["REGIONAL"]},
    )
    print(f"Created REST API: {api['id']}")
    return api["id"]


def get_root_resource_id(api_id):
    resources = apigw.get_resources(restApiId=api_id)["items"]
    for r in resources:
        if r["path"] == "/":
            return r["id"]
    raise RuntimeError("Root resource not found")


def get_or_create_resource(api_id, parent_id, path_part):
    resources = apigw.get_resources(restApiId=api_id)["items"]
    full_path = f"/{path_part}"
    for r in resources:
        if r["path"] == full_path:
            return r["id"]
    resource = apigw.create_resource(restApiId=api_id, parentId=parent_id, pathPart=path_part)
    return resource["id"]


def add_lambda_method_with_cors(api_id, resource_id, http_method, function_arn, function_name):
    region = config.AWS_REGION
    uri = f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{function_arn}/invocations"

    # --- the real method (POST) ---
    try:
        apigw.put_method(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod=http_method,
            authorizationType="NONE",
        )
    except apigw.exceptions.ConflictException:
        pass

    apigw.put_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod=http_method,
        type="AWS_PROXY",
        integrationHttpMethod="POST",
        uri=uri,
    )

    # Allow API Gateway to invoke this Lambda.
    try:
        lambda_client.add_permission(
            FunctionName=function_name,
            StatementId=f"apigw-invoke-{http_method.lower()}",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{region}:{ACCOUNT_ID}:{api_id}/*/*",
        )
    except lambda_client.exceptions.ResourceConflictException:
        pass

    # --- OPTIONS method for CORS preflight (MOCK integration) ---
    try:
        apigw.put_method(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod="OPTIONS",
            authorizationType="NONE",
        )
        apigw.put_integration(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod="OPTIONS",
            type="MOCK",
            requestTemplates={"application/json": '{"statusCode": 200}'},
        )
        apigw.put_method_response(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod="OPTIONS",
            statusCode="200",
            responseParameters={
                "method.response.header.Access-Control-Allow-Headers": False,
                "method.response.header.Access-Control-Allow-Methods": False,
                "method.response.header.Access-Control-Allow-Origin": False,
            },
        )
        apigw.put_integration_response(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod="OPTIONS",
            statusCode="200",
            responseParameters={
                "method.response.header.Access-Control-Allow-Headers": "'Content-Type'",
                "method.response.header.Access-Control-Allow-Methods": "'OPTIONS,POST'",
                "method.response.header.Access-Control-Allow-Origin": "'*'",
            },
        )
    except apigw.exceptions.ConflictException:
        pass


def deploy_stage(api_id):
    apigw.create_deployment(restApiId=api_id, stageName=config.API_STAGE_NAME)
    region = config.AWS_REGION
    return f"https://{api_id}.execute-api.{region}.amazonaws.com/{config.API_STAGE_NAME}"


if __name__ == "__main__":
    saved = state.load()
    for key in ("upload_function_arn", "query_function_arn"):
        if key not in saved:
            raise SystemExit(f"Missing '{key}' in state.json - run step3_lambdas.py first.")

    api_id = get_or_create_rest_api()
    root_id = get_root_resource_id(api_id)

    upload_resource_id = get_or_create_resource(api_id, root_id, "upload")
    ask_resource_id = get_or_create_resource(api_id, root_id, "ask")

    add_lambda_method_with_cors(
        api_id, upload_resource_id, "POST", saved["upload_function_arn"], config.LAMBDA_UPLOAD_FUNCTION_NAME
    )
    add_lambda_method_with_cors(
        api_id, ask_resource_id, "POST", saved["query_function_arn"], config.LAMBDA_QUERY_FUNCTION_NAME
    )

    invoke_url = deploy_stage(api_id)
    state.save({"api_id": api_id, "invoke_url": invoke_url})

    print("\nDone. API base URL:")
    print(f"  {invoke_url}")
    print(f"  Upload endpoint: {invoke_url}/upload")
    print(f"  Ask endpoint:    {invoke_url}/ask")
    print("\nPaste this base URL into frontend/app.js as API_BASE_URL.")
