"""
Private-platform / noindex hardening
====================================
IMS is a private internal business platform -- it must stay out of every search
index + AI crawler. The backend enforces this with (1) a /robots.txt that
disallows all crawlers and (2) an X-Robots-Tag: noindex header on EVERY response
(the frontend adds robots.txt + a noindex <meta> + a Vercel header too).
"""


def test_backend_robots_txt_disallows_all(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "Disallow: /" in r.text
    assert "User-agent: *" in r.text


def test_x_robots_tag_header_on_robots(client):
    r = client.get("/robots.txt")
    val = r.headers.get("x-robots-tag", "").lower()
    assert "noindex" in val
    assert "nofollow" in val


def test_x_robots_tag_header_on_api_response(client):
    # The header is added by middleware to EVERY response, not just /robots.txt.
    r = client.get("/api/v1/health")
    val = r.headers.get("x-robots-tag", "").lower()
    assert "noindex" in val
