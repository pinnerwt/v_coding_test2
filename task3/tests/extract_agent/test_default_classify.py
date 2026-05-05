from extract_agent.default_classify import classify_default


def test_short_ibr_phrase_in_head_classified_as_ibr():
    body = (
        "The information required by this Item is incorporated by reference to the Proxy Statement."
    )
    assert classify_default("Item 11", body) == "incorporated_by_reference"


def test_ibr_with_intervening_words():
    body = "Information is incorporated herein by reference to the 2023 Proxy."
    assert classify_default("Item 11", body) == "incorporated_by_reference"


def test_long_body_with_ibr_phrase_stays_extracted():
    body = "incorporated by reference. " + ("X" * 5000)
    assert classify_default("Item 7", body) == "extracted"


def test_reserved_title_classified():
    assert classify_default("[Reserved]", "") == "reserved"
    assert classify_default("Reserved", "Reserved.") == "reserved"


def test_not_applicable_short_body():
    assert classify_default("Mine Safety Disclosures", "Not applicable.") == "not_applicable"


def test_none_body_classified_as_not_applicable():
    assert classify_default("Item 4", "None.") == "not_applicable"
    assert classify_default("Item 4", "N/A") == "not_applicable"


def test_default_extracted():
    body = "We are a technology company. " * 50
    assert classify_default("Business", body) == "extracted"


def test_internal_xref_stays_extracted():
    # Per CLAUDE.md feedback memory: "Refer to Item N" is internal cross-ref, NOT IBR.
    assert classify_default("Item 11", "Refer to Item 10.") == "extracted"
    assert classify_default("Item 7", "Refer to Note 30.") == "extracted"
