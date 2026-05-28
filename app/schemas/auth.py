"""Pydantic schemas for OAuth callback handling."""

from pydantic import BaseModel


class GoogleUserInfo(BaseModel):
    """The Google `userinfo` payload we actually use.

    Google returns more fields; we only model what we read.
    """

    sub: str
    email: str
    name: str | None = None
    email_verified: bool = True
