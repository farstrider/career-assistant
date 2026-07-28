from __future__ import annotations

import http.cookiejar
import json
import os
import ssl
import urllib.error
import urllib.request

base_url = os.environ["CAREER_APP_BASE_URL"]
origin = base_url
password = os.environ.pop("CAREER_SMOKE_PASSWORD")
cookies = http.cookiejar.CookieJar()
replacement_password = "smoke-only-password-2026"  # pragma: allowlist secret
opener = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),  # noqa: S323
    urllib.request.HTTPCookieProcessor(cookies),
)


def request(
    path: str,
    method: str = "GET",
    body: dict[str, object] | None = None,
    csrf: str | None = None,
    extra_headers: dict[str, str] | None = None,
):
    headers = {"Accept": "application/json"}
    headers.update(extra_headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if method != "GET":
        headers["Origin"] = origin
        if csrf:
            headers["X-CSRF-Token"] = csrf
    response = opener.open(
        urllib.request.Request(base_url + path, data=data, headers=headers, method=method),
        timeout=10,
    )
    return response.status, json.load(response)


assert request("/api/v1/health/live")[1] == {"status": "ok"}
_, session = request(
    "/api/v1/auth/login",
    "POST",
    {"username": "smoke-admin", "password": password},
)
assert session["must_change_password"] is True
_, session = request(
    "/api/v1/auth/password",
    "POST",
    {
        "current_password": password,
        "new_password": replacement_password,
    },
    session["csrf_token"],
)
csrf = session["csrf_token"]
assert session["roles"] == ["member", "admin"]
assert request("/api/v1/health/ready")[1] == {"status": "ok"}
_, created = request(
    "/api/v1/admin/users",
    "POST",
    {"username": "smoke-member", "display_name": "Smoke Member"},
    csrf,
)
member_id = created["user"]["id"]
assert created["temporary_password"]
_, reset = request(f"/api/v1/admin/users/{member_id}/password-reset", "POST", csrf=csrf)
member_password = reset["temporary_password"]
request(f"/api/v1/admin/users/{member_id}/sessions/revoke", "POST", csrf=csrf)
request(
    f"/api/v1/admin/users/{member_id}",
    "PATCH",
    {"is_active": False},
    csrf,
)
request(
    f"/api/v1/admin/users/{member_id}",
    "PATCH",
    {"is_active": True},
    csrf,
)
_, jobs = request("/api/v1/jobs")
assert len(jobs["items"]) == 2
job_id = next(item["id"] for item in jobs["items"] if item["title"].startswith("Platform"))
assert len(request(f"/api/v1/jobs/{job_id}/versions")[1]) == 2
source = request("/api/v1/sources")[1][0]
assert source["enabled"] is False
assert source["latest_run"]["status"] == "failed"
assert source["latest_run"]["cursor_after"] is None
runs = request(f"/api/v1/sources/{source['id']}/runs")[1]
assert next(run for run in runs if run["status"] == "succeeded")["cursor_after"] == "complete"
request(
    f"/api/v1/jobs/{job_id}/feedback",
    "POST",
    {
        "event_type": "interested",
        "occurred_at": "2026-07-28T08:00:00Z",
        "note": "Admin profile feedback",
        "metadata": {},
    },
    csrf,
    {"Idempotency-Key": "smoke-feedback"},
)
assert len(request(f"/api/v1/jobs/{job_id}/feedback")[1]) == 1
try:
    request(
        f"/api/v1/admin/users/{session['user']['id']}",
        "PATCH",
        {"is_admin": False},
        csrf,
    )
except urllib.error.HTTPError as error:
    assert error.code == 409
else:
    raise AssertionError("final active administrator demotion was accepted")
request("/api/v1/auth/logout", "POST", csrf=csrf)
_, member_session = request(
    "/api/v1/auth/login",
    "POST",
    {"username": "smoke-member", "password": member_password},
)
_, member_session = request(
    "/api/v1/auth/password",
    "POST",
    {
        "current_password": member_password,
        "new_password": "member-smoke-password-2026",
    },  # pragma: allowlist secret
    member_session["csrf_token"],
)
assert request(f"/api/v1/jobs/{job_id}/feedback")[1] == []
request("/api/v1/auth/logout", "POST", csrf=member_session["csrf_token"])
assert b"Loading secure session" in opener.open(base_url + "/", timeout=10).read()
