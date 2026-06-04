"""HTMX-served web routes.

The human web surface is split into focused route modules; this file keeps the
single router that app.main mounts.
"""

from fastapi import APIRouter

from app.routes import web_analysis, web_lobby, web_player, web_viewer

router = APIRouter(tags=["web"])
router.include_router(web_lobby.router)
router.include_router(web_viewer.router)
router.include_router(web_analysis.router)
router.include_router(web_player.router)
