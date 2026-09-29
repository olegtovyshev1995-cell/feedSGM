"""Тесты выгрузки объявлений Auto.ru на подставном requester (без сети)."""

from __future__ import annotations

import csv
import json

import pytest

from scripts.autoru_export import AutoruApiError, AutoruClient, offer_to_row, write_outputs


def _offer(i):
    return {
        "id": f"{i}-abc",
        "category": "CARS",
        "status": "ACTIVE",
        "car_info": {"mark": "BMW", "model": "X5"},
        "documents": {"year": 2020, "vin": f"VIN{i}"},
        "price_info": {"price": 5_000_000, "currency": "RUR"},
        "state": {"mileage": 10000, "image_urls": [{}, {}]},
        "additional_info": {"creation_date": "1700000000000"},
    }


class FakeApi:
    def __init__(self, pages, statuses=None):
        self.pages = pages
        self.statuses = list(statuses or [])
        self.calls = []

    def __call__(self, method, url, headers, params, body):
        self.calls.append((method, url, headers, params, body))
        if self.statuses:
            return self.statuses.pop(0), {"error": "tmp"}
        if url.endswith("/auth/login"):
            return 200, {"session": {"id": "sess-1"}}
        page = params["page"]
        return 200, {
            "offers": self.pages[page - 1],
            "pagination": {"page": page, "total_page_count": len(self.pages)},
        }


def test_login_and_all_pages():
    api = FakeApi([[_offer(1), _offer(2)], [_offer(3)]])
    client = AutoruClient("Vertis key-123", requester=api, sleep=lambda s: None)
    client.login("u", "p")
    offers = list(client.iter_offers("all", page_size=2))

    assert [o["id"] for o in offers] == ["1-abc", "2-abc", "3-abc"]
    _, url, headers, params, _ = api.calls[-1]
    assert url == "https://apiauto.ru/1.0/user/offers/all"
    assert headers["x-authorization"] == "Vertis key-123"  # префикс не удваивается
    assert headers["x-session-id"] == "sess-1"
    assert params == {"page": 2, "page_size": 2}


def test_retry_then_success():
    api = FakeApi([[_offer(1)]], statuses=[503])
    client = AutoruClient("k", session_id="s", requester=api, sleep=lambda s: None)
    assert len(list(client.iter_offers())) == 1


def test_401_is_explained():
    api = FakeApi([[]], statuses=[401])
    client = AutoruClient("k", session_id="s", requester=api, sleep=lambda s: None)
    with pytest.raises(AutoruApiError, match="401"):
        list(client.iter_offers())


def test_no_session_fails_fast():
    client = AutoruClient("k", requester=FakeApi([[]]))
    with pytest.raises(AutoruApiError, match="сессии"):
        list(client.iter_offers())


def test_outputs(tmp_path):
    row = offer_to_row(_offer(1))
    assert row["mark"] == "BMW" and row["vin"] == "VIN1" and row["photos"] == 2
    json_path, csv_path = write_outputs([_offer(1)], tmp_path, "all")
    assert json.loads(json_path.read_text(encoding="utf-8"))[0]["id"] == "1-abc"
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig"), delimiter=";"))
    assert rows[0]["price"] == "5000000"


def test_latest_copy_and_env_file(tmp_path, monkeypatch):
    from scripts.autoru_export import _env_or_file

    write_outputs([_offer(1)], tmp_path, "all")
    assert (tmp_path / "autoru_offers_all_latest.json").is_file()
    assert (tmp_path / "autoru_offers_all_latest.csv").is_file()

    secret = tmp_path / "pw"
    secret.write_text("s3cret\n", encoding="utf-8")
    monkeypatch.delenv("AUTORU_PASSWORD", raising=False)
    monkeypatch.setenv("AUTORU_PASSWORD_FILE", str(secret))
    assert _env_or_file("AUTORU_PASSWORD") == "s3cret"


def test_inactive_filter_param_and_client_side():
    active, inactive = _offer(1), _offer(2)
    inactive["status"] = "INACTIVE"
    api = FakeApi([[active, inactive]])
    client = AutoruClient("k", session_id="s", requester=api, sleep=lambda s: None)
    offers = list(client.iter_offers("all", status="inactive"))

    assert [o["id"] for o in offers] == ["2-abc"]
    assert api.calls[-1][3]["status"] == "INACTIVE"


def test_main_inactive_writes_separate_file(tmp_path, monkeypatch):
    from scripts import autoru_export

    inactive = _offer(2)
    inactive["status"] = "INACTIVE"
    api = FakeApi([[_offer(1), inactive]])
    real_init = AutoruClient.__init__

    def fake_init(self, api_key, **kw):
        real_init(self, api_key, requester=api, sleep=lambda s: None, **kw)

    monkeypatch.setattr(AutoruClient, "__init__", fake_init)
    monkeypatch.setenv("AUTORU_API_KEY", "k")
    monkeypatch.setenv("AUTORU_SESSION_ID", "s")
    assert autoru_export.main(["--inactive", "--out", str(tmp_path)]) == 0
    data = json.loads((tmp_path / "autoru_offers_all_inactive_latest.json").read_text("utf-8"))
    assert [o["id"] for o in data] == ["2-abc"]
