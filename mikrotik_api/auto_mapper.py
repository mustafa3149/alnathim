"""Auto-Map MikroTik profiles to system packages (hybrid sync).

Phase: Final Architecture — when a MikroTik sync runs we:
  1. Pull all /ppp profile names and /ppp secret subscribers.
  2. Auto-detect existing package names (Economy/Plus/Standard/Turbo/More/Business
     or custom names that appear in MikroTik profiles).
  3. Create any missing package with a sensible price (based on rate-limit
     or a default 35_000 IQD when unknown).

This keeps the cloud DB consistent with the router at all times.
"""

import logging
import re

import database as db

log = logging.getLogger(__name__)

# Common MikroTik profile -> system package name mapping.
_PROFILE_PACKAGE_MAP = {
    "economy": "Economy",
    "plus": "Plus",
    "standard": "Standard",
    "turbo": "Turbo",
    "more": "More",
    "business": "Business",
    "بزنس برو": "Business Pro",
}

# Default package prices (IQD) for auto-created packages.
_DEFAULT_PRICES = {
    "Economy": 35000,
    "Plus": 45000,
    "Standard": 50000,
    "Turbo": 65000,
    "More": 75000,
    "Business": 100000,
    "Business Pro": 120000,
}

_RATE_LIMIT_RE = re.compile(r"(\d+)[mM]")


def _parse_rate_limit(profile):
    """Extract a Mbps value from a RouterOS rate-limit string like '20M/20M'."""
    rl = (profile or {}).get("rate-limit", "")
    m = _RATE_LIMIT_RE.search(rl)
    if m:
        return int(m.group(1))
    return None


def map_profile_to_package_name(profile_name):
    """Map a MikroTik profile name to a canonical system package name."""
    key = (profile_name or "").strip().lower().replace("_", " ").replace("-", " ")
    # Direct lookup first.
    if key in _PROFILE_PACKAGE_MAP:
        return _PROFILE_PACKAGE_MAP[key]
    # Try matching the first word to the known map (e.g. "Standard 50M").
    first_word = key.split(" ")[0]
    if first_word in _PROFILE_PACKAGE_MAP:
        return _PROFILE_PACKAGE_MAP[first_word]
    # Fallback: use the cleaned raw name in title case if plausible.
    cleaned = (profile_name or "").strip()
    return cleaned if cleaned else None


def ensure_package_from_profile(profile_name, rate_limit=None, price=None):
    """Create or find a system package matching a MikroTik profile.

    Args:
        profile_name: the RouterOS /ppp profile name.
        rate_limit: optional rate-limit string (e.g. '20M/20M').
        price: optional explicit price (IQD); falls back to rate-limit
            heuristic then DEFAULT prices.

    Returns:
        package id (int) — existing package's id or newly created package id.
    """
    pkg_name = map_profile_to_package_name(profile_name)
    if not pkg_name:
        log.warning("[AutoMap] Empty profile name — skipping package creation")
        return None

    existing = db.get_package_by_name(pkg_name)
    if existing:
        return existing["id"]

    # Guess a price from the rate-limit if possible.
    if price is None or price <= 0:
        speed = _parse_rate_limit({"rate-limit": rate_limit or ""})
        if speed:
            # Simple heuristic: speed Mbps -> price.
            price = max(25000, (speed or 20) * 1000)
        else:
            price = _DEFAULT_PRICES.get(pkg_name, 35000)

    speed_label = ""
    if rate_limit:
        speed_label = rate_limit
    new_id = db.add_package(name=pkg_name, price=price, speed=speed_label)
    log.info("[AutoMap] Created package %s (price=%d IQD, speed=%s) from MikroTik profile",
             pkg_name, price, speed_label)
    return new_id