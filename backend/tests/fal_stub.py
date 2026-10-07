"""A stand-in for fal.ai, plugged in at the httpx seam.

Every request GAM makes for a generation — submit, status, result, cancel, the pricing
API, and the download of each output — goes through an `httpx.Client`. The `fal`
fixture swaps that class for one wired to an `httpx.MockTransport` whose handler is a
`FakeFal`, so the real client code runs unchanged — headers, status codes, streaming,
redirects — and only the far end is pretend. The same seam `test_provider_upstream.py`
uses, with real httpx semantics instead of a hand-written client.

DNS is faked too, so the SSRF guard resolves names without touching the network, and
the job's clock and sleep are replaced so a thirty-minute deadline takes milliseconds.
"""

from __future__ import annotations

import json
import socket
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import pytest

from app.clock import utcnow
from app.generation import pricing
from app.generation import run as run_module

FIXTURES = Path(__file__).parent / "fixtures"

QUEUE = "https://queue.fal.run"
CDN = "https://v3.fal.media/files"
PUBLIC_ADDRESS = "93.184.216.34"


class ProcessDied(BaseException):
    """Raised from inside a request to stop a job dead, the way a restart would.

    A BaseException, so neither httpx, the job, nor the worker's catch-all catches it:
    the row is left exactly as a killed process leaves it.
    """


class FakeClock:
    def __init__(self) -> None:
        self.current = utcnow()
        self.slept: list[float] = []
        # Called on each sleep, for a test that acts while the job waits.
        self.on_sleep: Optional[Callable[[], None]] = None

    def now(self):
        return self.current

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.current += timedelta(seconds=seconds)
        if self.on_sleep is not None:
            self.on_sleep()


class FakeFal:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.request_id = "req-1"
        # Replaces the whole submit answer when set.
        self.submit_response: Optional[httpx.Response] = None
        # Answered in order; the last repeats. A dict is a 200 body; an httpx.Response
        # is sent as it is; a BaseException class is raised.
        self.statuses: list[Any] = [{"status": "COMPLETED"}]
        self.result_response: Optional[httpx.Response] = None
        self.result_body: dict = {
            "images": [{"url": f"{CDN}/out-0.png", "content_type": "image/png"}],
            "seed": 1234,
        }
        self.result_headers: dict = {
            "x-fal-billable-units": "1",
            "x-fal-request-id": "fal-req-1",
        }
        # fal's pricing API: a price for whatever endpoint is asked about, or a status.
        self.price: Optional[tuple[float, str]] = (0.025, "megapixel")
        self.pricing_status = 200
        self.pricing_raises: Optional[type] = None
        # url (without query) -> file to serve, or a ready response.
        self.files: dict[str, Any] = {f"{CDN}/out-0.png": FIXTURES / "sample_image.png"}
        # Host -> address, for the SSRF guard. Anything else is public.
        self.addresses: dict[str, str] = {}
        self.clock = FakeClock()

    # ─── the transport ───────────────────────────────────────────────────────

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        host, path, method = request.url.host, request.url.path, request.method

        if host == "queue.fal.run":
            if method == "POST":
                return self._submit(request)
            if method == "PUT" and path.endswith("/cancel"):
                return httpx.Response(202, json={"status": "CANCELLATION_REQUESTED"})
            if path.endswith("/status"):
                return self._status()
            return self.result_response or httpx.Response(
                200, json=self.result_body, headers=self.result_headers
            )

        if host == "api.fal.ai" and path == "/v1/models/pricing":
            if self.pricing_raises is not None:
                raise self.pricing_raises()
            if self.pricing_status != 200 or self.price is None:
                return httpx.Response(self.pricing_status if self.pricing_status != 200 else 404, json={
                    "error": {"type": "not_found", "message": "No price"}
                })
            endpoint_id = request.url.params.get("endpoint_id")
            unit_price, unit = self.price
            return httpx.Response(200, json={
                "prices": [{"endpoint_id": endpoint_id, "unit_price": unit_price, "unit": unit, "currency": "USD"}],
                "next_cursor": None,
                "has_more": False,
            })

        target = str(request.url.copy_with(query=None))
        served = self.files.get(target)
        if isinstance(served, httpx.Response):
            return served
        if isinstance(served, Path):
            return httpx.Response(200, content=served.read_bytes(), headers={"content-type": _media_type(served)})
        return httpx.Response(404, json={"detail": "Not Found"})

    def _submit(self, request: httpx.Request) -> httpx.Response:
        if self.submit_response is not None:
            return self.submit_response
        app = "/".join(request.url.path.strip("/").split("/")[:2])
        base = f"{QUEUE}/{app}/requests/{self.request_id}"
        return httpx.Response(200, json={
            "status": "IN_QUEUE",
            "request_id": self.request_id,
            "queue_position": 0,
            "response_url": base,
            "status_url": f"{base}/status",
            "cancel_url": f"{base}/cancel",
        })

    def _status(self) -> httpx.Response:
        answer = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
        if isinstance(answer, type) and issubclass(answer, BaseException):
            raise answer()
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json={"request_id": self.request_id, **answer})

    def getaddrinfo(self, host, port, *args, **kwargs):
        address = self.addresses.get(host, PUBLIC_ADDRESS)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    # ─── what was asked ──────────────────────────────────────────────────────

    def made(self, method: str, *, host: Optional[str] = None, suffix: str = "") -> list[httpx.Request]:
        return [
            r for r in self.calls
            if r.method == method
            and (host is None or r.url.host == host)
            and r.url.path.endswith(suffix)
        ]

    def submissions(self) -> list[dict]:
        return [json.loads(r.content) for r in self.made("POST", host="queue.fal.run")]

    def cancels(self) -> list[httpx.Request]:
        return self.made("PUT", host="queue.fal.run", suffix="/cancel")

    def downloads(self) -> list[httpx.Request]:
        return [r for r in self.calls if r.url.host not in ("queue.fal.run", "api.fal.ai")]


def _media_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".mp4": "video/mp4",
    }.get(path.suffix, "application/octet-stream")


@pytest.fixture
def fal(monkeypatch):
    fake = FakeFal()
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(fake), **kwargs)
    )
    monkeypatch.setattr(socket, "getaddrinfo", fake.getaddrinfo)
    monkeypatch.setattr(run_module, "sleep", fake.clock.sleep)
    monkeypatch.setattr(run_module, "now", fake.clock.now)
    pricing.clear_cache()
    yield fake
    pricing.clear_cache()


# ─── shared setup for the generation tests ───────────────────────────────────

TEST_USER = "user-under-test"
FAL_KEY = "fal-key-id:fal-key-secret"

T2I = "fal-ai/flux/dev"
I2I = "fal-ai/nano-banana/edit"
I2V = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"
VEO = "fal-ai/veo3.1/fast/image-to-video"


def seed_models(session) -> dict:
    """Four catalogue rows, shaped like the seeded ones they are named after.

    Built directly rather than through the migration: the test database comes from
    `create_all`, which seeds nothing. That the migration's own rows are well-formed is
    asserted in test_migrations.py.
    """
    from app.models.generation import GenerationModel

    rows = {
        T2I: GenerationModel(
            endpoint_id=T2I, kind="text_to_image", label="FLUX.1 [dev]", sort_order=20,
            options=json.dumps({
                "image_sizes": ["square_hd", "landscape_16_9"],
                "supports_seed": True,
                "max_outputs": 4,
            }),
            unit_price=0.025, price_unit="megapixel", price_currency="USD",
        ),
        I2I: GenerationModel(
            endpoint_id=I2I, kind="image_to_image", label="Nano Banana (edit)", sort_order=10,
            image_field="image_urls", image_field_is_list=True, max_images=3,
            options=json.dumps({
                "aspect_ratios": ["auto", "1:1", "16:9"],
                "supports_seed": True,
                "max_outputs": 4,
            }),
            unit_price=0.039, price_unit="image", price_currency="USD",
        ),
        I2V: GenerationModel(
            endpoint_id=I2V, kind="image_to_video", label="Kling 2.5 Turbo Pro", sort_order=10,
            image_field="image_url", max_images=1, end_image_field="tail_image_url",
            options=json.dumps({"durations": ["5", "10"], "supports_negative_prompt": True}),
            unit_price=0.07, price_unit="second", price_currency="USD",
        ),
        VEO: GenerationModel(
            endpoint_id=VEO, kind="image_to_video", label="Veo 3.1 Fast", sort_order=30,
            image_field="image_url", max_images=1,
            options=json.dumps({
                "durations": ["4s", "6s", "8s"],
                "resolutions": ["720p", "1080p"],
                "supports_seed": True,
                "supports_audio": True,
                "supports_negative_prompt": True,
            }),
            extra_params=json.dumps({"generate_audio": False}),
            unit_price=0.10, price_unit="second", price_currency="USD",
        ),
    }
    for row in rows.values():
        session.add(row)
    session.commit()
    for row in rows.values():
        session.refresh(row)
    return rows


def set_fal_key(session, user_id: str = TEST_USER) -> None:
    from app.settings_store import FAL_API_KEY, set_setting

    set_setting(session, user_id, FAL_API_KEY, FAL_KEY)


def upload(client, name: str, media_type: str) -> dict:
    response = client.post(
        "/api/assets",
        files=[("files", (name, (FIXTURES / name).read_bytes(), media_type))],
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["created"][0]


def run_job(session, monkeypatch, job_id: str):
    """Run a queued generation on this thread, against the test database."""
    from app.jobs import enrichment as enrichment_jobs
    from app.models.job import EnrichmentJob

    queue = enrichment_jobs.generation_queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_generation_job(job_id)
    session.expire_all()
    return session.get(EnrichmentJob, job_id)
