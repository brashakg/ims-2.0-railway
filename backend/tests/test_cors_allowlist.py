"""SEC-CORS-WILDCARD (BUG-114): _is_allowed_origin must trust ONLY exact prod
hosts, the owner's anchored Vercel previews, and true uniparallel subdomains --
never a bare *.vercel.app / *.up.railway.app substring (which made any
attacker-controlled deployment a credentialed cross-origin)."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
# Pin the preview suffix so the battery is deterministic regardless of env.
os.environ["VERCEL_PREVIEW_SUFFIX"] = "-avinashs-projects-b3cb6df8.vercel.app"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import _is_allowed_origin  # noqa: E402

ALLOWED = [
    "https://ims-2-0-railway.vercel.app",            # prod alias (exact)
    "https://ims-20-railway.vercel.app",             # prod alias (exact)
    "https://ims-20-railway-production.up.railway.app",  # railway backend (exact)
    "https://uniparallel.com",                       # apex
    "https://app.uniparallel.com",                   # true subdomain
    "https://api.uniparallel.com",
    "http://localhost:3000",                         # dev (exact)
    "https://ims-2-0-railway-git-main-avinashs-projects-b3cb6df8.vercel.app",   # owner preview
    "https://ims-2-0-railway-k3j9f-avinashs-projects-b3cb6df8.vercel.app",      # owner preview
]

BLOCKED = [
    "https://evil.vercel.app",                            # attacker's own vercel
    "https://ims-2-0-railway.vercel.app.attacker.com",    # the old substring hole
    "https://x.vercel.app.attacker.com",
    "https://evil.up.railway.app",                        # blanket railway removed
    "https://eviluniparallel.com",                        # not a true subdomain
    "https://uniparallel.com.attacker.com",
    "http://ims-2-0-railway.vercel.app",                  # non-https
    "https://x-avinashs-projects-b3cb6df8.vercel.app.evil.com",  # suffix not at the end
    "",
    None,
]


def test_legit_origins_allowed():
    for o in ALLOWED:
        assert _is_allowed_origin(o) is True, f"should ALLOW {o}"


def test_attacker_origins_blocked():
    for o in BLOCKED:
        assert _is_allowed_origin(o) is not True, f"should BLOCK {o}"
