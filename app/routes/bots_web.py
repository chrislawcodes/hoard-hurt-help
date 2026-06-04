"""Aggregate router for the self-serve "My Bots" control panel."""

from fastapi import APIRouter

from app.routes import (
    bots_credentials,
    bots_lifecycle,
    bots_setup,
    bots_status,
)

router = APIRouter(tags=["bots"])
router.include_router(bots_setup.router, prefix="/me/bots")
router.include_router(bots_status.router, prefix="/me/bots")
router.include_router(bots_credentials.router, prefix="/me/bots")
router.include_router(bots_lifecycle.router, prefix="/me/bots")
