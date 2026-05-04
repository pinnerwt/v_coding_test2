from sec_toolbox.taxonomy import items_for_year


def test_2023_includes_item_1c():
    items = items_for_year(2023)
    nums = [i.item_number for i in items]
    assert "1C" in nums  # Cybersecurity, added 2023


def test_2019_excludes_item_1c():
    items = items_for_year(2019)
    nums = [i.item_number for i in items]
    assert "1C" not in nums


def test_2022_item_6_reserved():
    item6 = next(i for i in items_for_year(2022) if i.item_number == "6")
    assert item6.is_reserved_default


def test_2018_item_6_selected_financial_data():
    item6 = next(i for i in items_for_year(2018) if i.item_number == "6")
    assert "Selected Financial Data" in item6.canonical_title
    assert not item6.is_reserved_default


def test_part_assignment():
    items = items_for_year(2023)
    by_num = {i.item_number: i for i in items}
    assert by_num["1"].part == "I"
    assert by_num["5"].part == "II"
    assert by_num["10"].part == "III"
    assert by_num["15"].part == "IV"


def test_includes_classic_items():
    items = items_for_year(2023)
    nums = {i.item_number for i in items}
    # Core Items required on every modern 10-K
    for expected in [
        "1",
        "1A",
        "1B",
        "2",
        "3",
        "4",
        "5",
        "7",
        "7A",
        "8",
        "9",
        "9A",
        "9B",
        "10",
        "11",
        "12",
        "13",
        "14",
        "15",
    ]:
        assert expected in nums, f"missing item {expected}"
