"""Ingestion logic, tested without network: the API is replaced by fakes."""

from datetime import UTC, datetime, timedelta

import pytest

from ingest import backfill_cons_def, incremental, odre

NOW = datetime(2026, 11, 14, 6, 0, tzinfo=UTC)


def test_backfill_chunks_cover_every_year_exactly_once():
    filters = [backfill_cons_def.year_filter(y, 2026) for y in range(2012, 2027)]
    assert filters[0] == "date_heure < date'2013-01-01T00:00:00Z'"         # also takes 2011-12-31 23:xx UTC
    assert filters[-1] == "date_heure >= date'2026-01-01T00:00:00Z'"       # open-ended: latest data
    for year, (this_chunk, next_chunk) in zip(range(2012, 2026), zip(filters, filters[1:])):
        boundary = f"date'{year + 1}-01-01T00:00:00Z'"
        assert f"date_heure < {boundary}" in this_chunk                    # this chunk stops at the boundary
        assert f"date_heure >= {boundary}" in next_chunk                   # the next one starts exactly there


@pytest.fixture
def fake_api(monkeypatch):
    """Replace the two network-touching functions. Returns (download calls, mutable fingerprint)."""
    downloads = []

    def fake_save_export(client, dataset, where, final, extracted_at):
        downloads.append(dataset)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"fake parquet")
        return {"file": final.name, "where": where, "rows": 1,
                "extracted_at": extracted_at.isoformat(), "sha256": "fake"}

    fingerprint = {"value": [{"nature": "Données consolidées", "n": 23328}]}
    monkeypatch.setattr(odre, "save_export", fake_save_export)
    monkeypatch.setattr(incremental, "fingerprint", lambda client, where: fingerprint["value"])
    return downloads, fingerprint


def run_daily_job(raw_root, now):
    return incremental.capture_tr(None, raw_root, now), incremental.capture_cons_def(None, raw_root, now)


def test_second_run_on_the_same_day_writes_nothing(tmp_path, fake_api):
    downloads, _ = fake_api
    files_before = set()

    assert run_daily_job(tmp_path, NOW) == ("written", "written")
    files_before = {p for p in tmp_path.rglob("*") if p.is_file()}

    assert run_daily_job(tmp_path, NOW + timedelta(hours=5)) == ("skipped", "skipped")
    assert {p for p in tmp_path.rglob("*") if p.is_file()} == files_before
    assert downloads == [odre.TR, odre.CONS_DEF]


def test_cons_def_is_only_downloaded_when_its_fingerprint_changes(tmp_path, fake_api):
    downloads, fingerprint = fake_api
    run_daily_job(tmp_path, NOW)

    # Next day, nothing changed upstream: tr is captured again, cons-def is not.
    assert run_daily_job(tmp_path, NOW + timedelta(days=1)) == ("written", "unchanged")

    # RTE publishes a new consolidated month: the fingerprint changes and cons-def is downloaded.
    fingerprint["value"] = [{"nature": "Données consolidées", "n": 26208}]
    assert run_daily_job(tmp_path, NOW + timedelta(days=2)) == ("written", "written")

    assert downloads.count(odre.CONS_DEF) == 2
    assert downloads.count(odre.TR) == 3


def test_retries_transient_errors_but_not_client_errors(monkeypatch):
    import httpx

    monkeypatch.setattr(odre.time, "sleep", lambda seconds: None)
    request = httpx.Request("GET", "https://example.invalid")
    errors = [httpx.ConnectError("down", request=request),
              httpx.HTTPStatusError("503", request=request, response=httpx.Response(503, request=request))]

    def flaky():
        if errors:
            raise errors.pop(0)
        return "ok"

    assert odre.with_retries(flaky) == "ok"

    def bad_request():
        raise httpx.HTTPStatusError("400", request=request, response=httpx.Response(400, request=request))

    with pytest.raises(httpx.HTTPStatusError):
        odre.with_retries(bad_request)
