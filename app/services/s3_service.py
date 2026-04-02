"""AWS S3 PDF 업로드/presigned URL/삭제 서비스."""

import boto3
from botocore.exceptions import ClientError
from loguru import logger

from app.config import settings


def _s3_client():
    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        endpoint_url=f"https://s3.{settings.AWS_REGION}.amazonaws.com",
    )


def upload_pdf(pdf_bytes: bytes, s3_key: str) -> str:
    """S3에 PDF를 업로드하고 presigned URL을 반환한다.

    Args:
        pdf_bytes: PDF 바이트
        s3_key: S3 오브젝트 키 (예: "uploads/abc123.pdf")

    Returns:
        RunPod이 접근할 수 있는 presigned URL
    """
    client = _s3_client()
    client.put_object(
        Bucket=settings.AWS_S3_BUCKET,
        Key=s3_key,
        Body=pdf_bytes,
        ContentType="application/pdf",
    )
    logger.info(f"S3 업로드 완료 - bucket={settings.AWS_S3_BUCKET}, key={s3_key}")

    presigned_url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.AWS_S3_BUCKET, "Key": s3_key},
        ExpiresIn=settings.AWS_S3_PRESIGNED_URL_EXPIRES,
    )
    return presigned_url


def delete_pdf(s3_key: str) -> None:
    """S3에서 PDF를 삭제한다.

    Args:
        s3_key: S3 오브젝트 키
    """
    client = _s3_client()
    try:
        client.delete_object(Bucket=settings.AWS_S3_BUCKET, Key=s3_key)
        logger.info(f"S3 삭제 완료 - key={s3_key}")
    except ClientError as e:
        logger.warning(f"S3 삭제 실패 - key={s3_key}, error={e}")
