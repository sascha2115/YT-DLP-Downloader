"""Site profiles, URL detection and normalization (site-aware)."""

import re
from urllib.parse import urlparse


YOUTUBE_ID_REGEX = re.compile(
    r"(?:v=|\/|embed\/|shorts\/|live\/)([a-zA-Z0-9_-]{11})(?:[?&/ ]|$)"
)

RUMBLE_ID_REGEX = re.compile(
    r"rumble\.com/(?:embed/)?(v[a-zA-Z0-9]+)(?:[?&/ -]|$)"
)

# ----------------------------------------------------------------------------------------------------
# Supported sites (proof-of-concept: YouTube + Rumble)
# Each entry defines how the site is detected and which features it supports.
# ----------------------------------------------------------------------------------------------------
SUPPORTED_SITES = {
    "youtube": {
        "label": "YouTube",
        "domains": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
        "sponsorblock": True,
        "js_runtime": True,  # yt-dlp evaluates YouTube's JS sig/nsig challenges
        "resync_auto_subs": True,  # YouTube ASR captions arrive as choppy fragments
        "id_regex": YOUTUBE_ID_REGEX,
    },
    "rumble": {
        "label": "Rumble",
        "domains": ("rumble.com",),
        "sponsorblock": False,  # SponsorBlock is YouTube-only
        "js_runtime": False,  # the Rumble extractor needs no JS runtime
        "resync_auto_subs": False,  # Rumble's subs are already well-formatted
        "id_regex": RUMBLE_ID_REGEX,
    },
}

# Default site profile for unknown domains (yt-dlp may still support them)
DEFAULT_SITE = "youtube"

# Human-readable site list for the info panel header, e.g. "YouTube, Rumble"
SUPPORTED_SITES_LABEL = ", ".join(p["label"] for p in SUPPORTED_SITES.values())

def detect_site(url):
    """
    Detect which supported site profile a URL belongs to.

    Returns a key into SUPPORTED_SITES ("youtube", "rumble", ...).
    Unknown domains fall back to DEFAULT_SITE (YouTube), preserving the
    historical behavior where any non-matching input was treated as YouTube.
    """
    if not url:
        return DEFAULT_SITE
    # urlparse only yields a hostname for scheme URLs; prepend one for
    # schemeless input like "rumble.com/vXXXXXX" or "RUMBLE.COM/..."
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        host = urlparse(candidate).hostname or ""
    except ValueError:
        return DEFAULT_SITE
    host = host.lower().removeprefix("www.")
    for site_key, profile in SUPPORTED_SITES.items():
        for domain in profile["domains"]:
            if host == domain or host.endswith("." + domain):
                return site_key
    return DEFAULT_SITE


def site_resyncs_auto_subs(site):
    """
    Whether auto-generated subtitles of a site need the 2-line merge.

    YouTube's ASR captions arrive as choppy fragments that the resync/merge
    step reformats; sites like Rumble deliver them already well-formatted.
    Site profiles opt out via the "resync_auto_subs" key. Defaults to True
    so unknown/future sites keep the previous always-merge behavior.
    """
    return SUPPORTED_SITES.get(site, {}).get("resync_auto_subs", True)


def site_wants_js_runtime(site):
    """
    Whether yt-dlp benefits from an external JS runtime (deno) for a site.

    Only extractors that evaluate JavaScript need one (YouTube's signature
    challenges); site profiles opt out via the "js_runtime" key. Defaults to
    True so unknown/future sites keep the previous always-pass behavior.
    """
    return SUPPORTED_SITES.get(site, {}).get("js_runtime", True)


def is_plausible_url(url):
    """
    Cheap syntactic pre-check so obvious non-URLs never reach yt-dlp.

    yt-dlp's generic extractor turns anything it cannot classify into an
    ugly "[generic] 'nonsense' is not a valid URL" subprocess error; this
    keeps typos and garbage out of the yt-dlp call entirely. Deliberately
    permissive: any well-formed dotted hostname (or localhost) passes -
    yt-dlp itself decides whether the site is actually supported.
    """
    if not url:
        return False
    url = str(url).strip()
    # Naked 11-char YouTube ID (normalize_url canonicalizes these too)
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
        return True
    if "://" not in url:
        url = "https://" + url
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost":
        return True
    # The hostname must be well-formed: dot-separated labels of letters,
    # digits and hyphens. urlparse is lenient and happily returns hostnames
    # with spaces (e.g. clipboard text like "YT-DLP Downloader 1.1.25"
    # parses as host "yt-dlp downloader 1.1.25"), so an explicit shape
    # check is needed on top of the dot requirement.
    if not re.fullmatch(
        r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*",
        host,
    ):
        return False
    # Single-label hosts ("nonsense") are not URLs; a dot is required.
    return "." in host


# ----------------------------------------------------------------------------------------------------
# Normalize (clean) a YouTube URL
# ----------------------------------------------------------------------------------------------------
def normalize_url(url):
    # Normalize (and if needed, extract) a video URL from user input.
    #
    # The URL field may contain extra surrounding text (e.g. copied from chat):
    #   mytext `https://www.youtube.com/watch?v=xxxxxxxxxxx`
    # In that case we want to extract the actual URL/id instead of passing the
    # whole string to yt-dlp.
    if not url:
        return ""
    # Remove extra whitespace and normalize
    url = re.sub(r"\s+", " ", str(url)).strip()

    # 1) Extract the first URL-like token if there is any scheme URL inside the text.
    m = re.search(r"(https?://[^\s]+)", url, flags=re.IGNORECASE)
    if m:
        url = m.group(1)
    else:
        # 2) Extract a known-site domain token without scheme, e.g.
        #    "www.youtube.com/..." or "rumble.com/vXXXXXX-title.html"
        m = re.search(
            r"((?:www\.)?(?:youtube\.com|youtu\.be|rumble\.com)/[^\s]+)",
            url,
            flags=re.IGNORECASE,
        )
        if m:
            url = "https://" + m.group(1)

    # Strip common wrappers / trailing punctuation from copied text
    url = url.strip("`\"'<>[](){}.,;")

    # Rumble URLs: strip trailing punctuation but keep query params intact.
    # Nothing below this point is YouTube-specific, so we're done.
    if detect_site(url) == "rumble":
        return url

    # If it's a naked 11-char YouTube ID, make it a full URL
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
        return f"https://www.youtube.com/watch?v={url}"

    # If the input still isn't a clean URL, but contains a recognizable YouTube ID,
    # canonicalize it to a watch URL (handles cases like trailing backticks, etc.).
    if m := re.search(
        r"(?:v=|embed/|shorts/|live/|youtu\.be/)([a-zA-Z0-9_-]{11})",
        url,
        flags=re.IGNORECASE,
    ):
        return f"https://www.youtube.com/watch?v={m.group(1)}"

    # Remove query parameters after first & (keeps ?v=... but removes &list=... etc)
    url = re.sub(r"&.*$", "", url)
    return url
