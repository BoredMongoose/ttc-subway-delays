"""Download the TTC subway delay logs from the City of Toronto Open Data portal (CKAN API).

Files land in data/raw/. The Excel files cover January 2014 to December 2024; from 2025 the City publishes
one growing CSV ("since 2025"), which is re-downloaded on every run. Other files are skipped if present.
"""
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
API = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show"
PACKAGE = "ttc-subway-delay-data"


def wanted(res):
    """Yearly Excel logs, the since-2025 CSV and the code lookups; skip the XML/JSON duplicates."""
    if res.get("datastore_active") and not res.get("size"):
        return False                                  # live datastore dump, duplicated by the CSV
    return (res.get("format") or "").upper() in ("XLSX", "CSV")


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    meta = requests.get(API, params={"id": PACKAGE}, timeout=60).json()["result"]
    for res in filter(wanted, meta["resources"]):
        fname = res["url"].rsplit("/", 1)[-1]
        path = RAW / fname
        if path.exists() and "since-2025" not in fname:
            continue
        r = requests.get(res["url"], timeout=300)
        r.raise_for_status()
        path.write_bytes(r.content)
        print(f"{fname:55s} {len(r.content) / 1e6:6.2f} MB")
    print("portal last refreshed", meta.get("last_refreshed", "")[:10])


if __name__ == "__main__":
    main()
