"""Fetch small, attributed public-data subsets. Run explicitly; never during app startup.

Only the six compressed status CSV members are read from the 98 MB Kelmarsh
archive using HTTP byte ranges. No SCADA sensor archive is downloaded.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import random
import re
import struct
import urllib.request
import zipfile
import zlib
from datetime import datetime, timezone

DEST = Path(__file__).resolve().parents[1] / "data" / "external"
KELMARSH_URL = "https://zenodo.org/records/16807551/files/Kelmarsh_SCADA_2016_3082.zip?download=1"
MAINTIE_REPO = "https://github.com/nlp-tlp/maintie"


def get(url: str, **headers) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "FixList-public-data-import/1.0", **headers}), timeout=45) as response:
        if "Range" in headers and response.status != 206:
            raise RuntimeError("Server must support byte ranges; refusing to download the full archive.")
        return response.read(4_000_001)


def status_members():
    tail = get(KELMARSH_URL, Range="bytes=-65557")
    position = tail.rfind(b"PK\x05\x06")
    if position < 0:
        raise RuntimeError("ZIP directory not found")
    record = struct.unpack("<4s4H2LH", tail[position:position + 22])
    size, offset = record[5:7]
    directory = get(KELMARSH_URL, Range=f"bytes={offset}-{offset + size - 1}")
    end_record = b"PK\x05\x06" + struct.pack("<4H2LH", 0, 0, record[3], record[4], size, 0, 0)
    archive = zipfile.ZipFile(io.BytesIO(directory + end_record))
    for member in archive.infolist():
        if not member.filename.startswith("Status_Kelmarsh_") or not member.filename.endswith(".csv"):
            continue
        if member.file_size > 2_000_000 or member.compress_size > 1_000_000:
            raise RuntimeError("Unexpectedly large status file")
        block = get(KELMARSH_URL, Range=f"bytes={member.header_offset}-{member.header_offset + member.compress_size + 4095}")
        header = struct.unpack("<4s5H3L2H", block[:30])
        start = 30 + header[-2] + header[-1]
        compressed = block[start:start + member.compress_size]
        content = zlib.decompress(compressed, -15) if member.compress_type == 8 else compressed
        if len(content) != member.file_size or zlib.crc32(content) != member.CRC:
            raise RuntimeError(f"CRC mismatch: {member.filename}")
        yield member.filename, content


def save(name, payload):
    content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (DEST / name).write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    events, sources, source_rows, excluded = [], [], 0, []
    for filename, content in status_members():
        turbine = int(re.search(r"Status_Kelmarsh_(\d+)_", filename).group(1))
        lines = content.decode("utf-8-sig").splitlines()
        records = list(csv.DictReader(io.StringIO("\n".join(line for line in lines if not line.startswith("#")))))
        source_rows += len(records)
        sources.append({"file": filename, "sha256": hashlib.sha256(content).hexdigest(), "rows": len(records)})
        for number, row in enumerate(records, 1):
            if row["Status"] != "Stop" or row["IEC category"] != "Forced outage":
                continue
            if any(row[key].strip() in ("", "-") for key in ("Timestamp start", "Timestamp end", "Duration")):
                excluded.append({"file": filename, "row": number, "reason": "Missing start, end or duration; not treated as zero downtime."})
                continue
            start, end = (datetime.fromisoformat(row[key]).replace(tzinfo=timezone.utc) for key in ("Timestamp start", "Timestamp end"))
            seconds = int((end - start).total_seconds())
            if seconds <= 0:
                excluded.append({"file": filename, "row": number, "reason": "Nonpositive event duration."})
                continue
            hh, mm, ss = map(int, row["Duration"].split(":"))
            if abs(seconds - (hh * 3600 + mm * 60 + ss)) > 1:
                raise RuntimeError(f"Duration mismatch in {filename}, row {number}")
            events.append({
                "id": f"KEL2016-{turbine}-{number:04d}", "equipment_id": f"KEL{turbine:02d}",
                "equipment_name": f"Kelmarsh {turbine}", "turbine_model": "Senvion MM92",
                "started_at": start.isoformat(), "ended_at": end.isoformat(),
                "duration_seconds": seconds, "source_duration": row["Duration"],
                "status": row["Status"], "code": row["Code"], "message": row["Message"],
                "comment": row["Comment"], "service_category": row["Service contract category"],
                "iec_category": row["IEC category"], "source_file": filename, "source_row": number,
            })
    if len(sources) != 6 or not events:
        raise RuntimeError("Expected six turbines with nonempty events")
    events.sort(key=lambda row: (row["started_at"], row["equipment_id"], row["id"]))
    kel_sha = save("kelmarsh_events.json", events)

    commit = json.loads(get("https://api.github.com/repos/nlp-tlp/maintie/commits/main"))["sha"]
    root = f"https://raw.githubusercontent.com/nlp-tlp/maintie/{commit}"
    raw = get(root + "/data/gold_release.json")
    gold = json.loads(raw)
    terms = re.compile(r"\b(pump|motor|engine|bearing|valve|hydraulic|oil|filter|leak|gear|coolant|compressor|belt)\w*\b", re.I)
    candidates = [(index, item) for index, item in enumerate(gold) if terms.search(item["text"]) and len(item["tokens"]) >= 3 and len(item["entities"]) >= 2]
    chosen = sorted(random.Random(42).sample(candidates, min(80, len(candidates))), key=lambda pair: pair[0])
    examples = [{"id": f"MAINTIE-GOLD-{index + 1:04d}", "source_index": index, **item} for index, item in chosen]
    maintie_sha = save("maintie_examples.json", examples)
    (DEST / "MaintIE_LICENSE.md").write_bytes(get(root + "/LICENSE.md"))
    manifest = {
        "schema_version": 1, "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kelmarsh": {
            "title": "Kelmarsh wind farm data", "version": "v4", "doi": "10.5281/zenodo.16807551",
            "url": "https://zenodo.org/records/16807551", "download_url": KELMARSH_URL,
            "creators": ["Charlie Plumley", "Roberta Takeuchi"], "publisher": "Cubico Sustainable Investments Ltd",
            "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "coverage_start": "2016-01-01T00:00:00+00:00", "coverage_end": "2017-01-01T00:00:00+00:00", "timezone": "UTC",
            "equipment_count": 6, "count": len(events), "source_rows": source_rows, "sources": sources, "excluded": excluded,
            "selection": "All complete, positive-duration rows with Status=Stop and IEC category=Forced outage from all six 2016 status CSV files. No event subsampling or date shifting. Incomplete rows listed in excluded.",
            "changes": "CSV to JSON; stable source-row IDs, UTC timestamps and checked durations in seconds added. Messages and original fields retained. No repair costs or causes inferred.",
            "sha256": kel_sha,
        },
        "maintie": {
            "title": "MaintIE: Maintenance Information Extraction", "url": MAINTIE_REPO, "commit": commit,
            "download_url": root + "/data/gold_release.json", "license": "MIT", "license_file": "MaintIE_LICENSE.md",
            "creators": ["Tyler K. Bikaun", "Tim French", "Michael Stewart", "Wei Liu", "Melinda Hodkiewicz"],
            "count": len(examples), "source_count": len(gold), "source_sha256": hashlib.sha256(raw).hexdigest(),
            "selection": "80 gold examples about industrial components, at least 3 tokens and 2 entities; deterministic random sample with seed=42. A curated demo subset, not an unbiased benchmark.",
            "changes": "Original text, tokens, entities and relations unchanged; stable IDs and zero-based source indexes added. No dates, equipment identifiers, downtime or costs added.",
            "sha256": maintie_sha,
        },
    }
    save("manifest.json", manifest)
    print(json.dumps({"kelmarsh_events": len(events), "turbines": 6, "maintie_examples": len(examples), "maintie_commit": commit, "directory": str(DEST)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
