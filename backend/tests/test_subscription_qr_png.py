"""Проверка PNG личной страницы без Pillow."""
import base64
import re

import png
import pytest

from app.models.client import Client
from app.services.subscription_page import render_subscription_page
from conftest import TestingSessionLocal


@pytest.mark.asyncio
async def test_subscription_page_embeds_valid_black_and_white_png():
    async with TestingSessionLocal() as db:
        client = Client(name="PNG client", phone="", email="", is_active=True)
        db.add(client)
        await db.commit()
        html = render_subscription_page(
            client, [], {}, set(), "https://panel.example/sub/test-subscription", 0, 0
        )
    encoded = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", html).group(1)
    image = base64.b64decode(encoded)
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    width, height, rows, metadata = png.Reader(bytes=image).read()
    assert width == height and width > 100
    assert metadata["greyscale"] and metadata["bitdepth"] == 1
    assert {pixel for row in rows for pixel in row} == {0, 1}
