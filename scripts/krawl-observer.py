"""Pinned Krawl adapter: observe only, bounded pages, no external ban actions."""
import os
from config import get_config

config = get_config()
config._server_ip = os.environ.get('MD_SERVER_IP')
config._server_ip_resolved = True

from app import app
from middleware.ban_check import BanCheckMiddleware
from middleware.deception import DeceptionMiddleware

app.user_middleware = [entry for entry in app.user_middleware if entry.cls not in (BanCheckMiddleware, DeceptionMiddleware)]
