"""
deploy/aws_session.py

Small helper so every deploy script uses the same boto3 session
(region + profile) defined in config.py.
"""

import boto3

import config


def get_session():
    kwargs = {"region_name": config.AWS_REGION}
    if config.AWS_PROFILE:
        kwargs["profile_name"] = config.AWS_PROFILE
    return boto3.Session(**kwargs)
