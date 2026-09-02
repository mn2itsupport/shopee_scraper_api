from app.db.client import get_supabase

_site_id_cache: dict[str, str] = {}


def get_site_id(site_key: str) -> str | None:
    if site_key in _site_id_cache:
        return _site_id_cache[site_key]

    result = get_supabase().table("sites").select("id").eq("site_key", site_key).limit(1).execute()
    if not result.data:
        return None  # not cached: a site added after startup is picked up on its next call

    site_id = result.data[0]["id"]
    _site_id_cache[site_key] = site_id
    return site_id
