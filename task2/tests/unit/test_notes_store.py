from agent.notes_store import NotesStore


def test_append_and_get(tmp_path):
    s = NotesStore(tmp_path / "n.db")
    s.append("https://a.test/x", "first")
    s.append("https://a.test/x", "second")
    assert s.get("https://a.test/x") == "first\nsecond"


def test_query_string_stripped_by_default(tmp_path):
    s = NotesStore(tmp_path / "n.db")
    s.append("https://a.test/x?token=1", "one")
    assert s.get("https://a.test/x?token=2") == "one"


def test_query_string_kept_when_disabled(tmp_path):
    s = NotesStore(tmp_path / "n.db", query_strip=False)
    s.append("https://a.test/x?a=1", "a")
    assert s.get("https://a.test/x?a=2") == ""


def test_cap_2kb(tmp_path):
    s = NotesStore(tmp_path / "n.db")
    line = "x" * 200
    for _ in range(50):
        s.append("https://a.test/", line)
    assert len(s.get("https://a.test/")) <= 2048
