"""Handling unit (HU) model and SSCC helper tests."""
from vla import model as M
from vla.db import get_db, COLLECTIONS


def test_handling_units_collection_exists():
    assert "dw_handling_units" in COLLECTIONS
    db = get_db(mongo_url=None)
    assert db.dw_handling_units.count_documents({}) == 0


def test_sscc_check_digit_known_value():
    # GS1 reference: 17-digit base 00000000000000001 -> check digit 7
    assert M.sscc_check_digit("0" * 16 + "1") == 7


def test_new_hu_id_shape_and_checksum():
    hid = M.new_hu_id()
    assert len(hid) == 18 and hid.startswith("80") and hid.isdigit()
    assert int(hid[-1]) == M.sscc_check_digit(hid[:17])
    assert M.new_hu_id() != hid  # unique enough for a demo


def test_hu_constants():
    assert M.HU_STATUSES == ["wrapped", "stored", "awaiting_shipment", "shipped"]
    assert M.LOC_COLDSTORE == "koelmagazijn" and M.LOC_EXPEDITION == "expeditie"
