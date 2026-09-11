import asyncio

from src import config


class SesEmailSender:
    @property
    def is_configured(self) -> bool:
        return bool(config.EMAIL_FROM)

    async def send_password_reset(self, recipient: str, reset_url: str) -> None:
        if not self.is_configured:
            raise RuntimeError("Password reset email delivery is not configured")
        await asyncio.to_thread(self._send_password_reset, recipient, reset_url)

    @staticmethod
    def _send_password_reset(recipient: str, reset_url: str) -> None:
        # Lazy import avoids initializing AWS clients for unrelated requests.
        import boto3

        client = boto3.client(
            "ses",
            region_name=config.AWS_REGION,
            aws_access_key_id=config.AWS_ACCESS_KEY_ID or None,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY or None,
        )
        client.send_email(
            Source=config.EMAIL_FROM,
            Destination={"ToAddresses": [recipient]},
            Message={
                "Subject": {"Data": "삐빅 비밀번호 재설정", "Charset": "UTF-8"},
                "Body": {
                    "Text": {
                        "Data": (
                            "아래 링크에서 비밀번호를 재설정해 주세요.\n\n"
                            f"{reset_url}\n\n"
                            f"이 링크는 {config.PASSWORD_RESET_EXPIRE_MINUTES}분 동안 한 번만 사용할 수 있습니다."
                        ),
                        "Charset": "UTF-8",
                    }
                },
            },
        )
