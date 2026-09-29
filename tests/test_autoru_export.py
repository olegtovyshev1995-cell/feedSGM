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
