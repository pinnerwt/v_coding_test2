from pathlib import Path

from ..cleaner import clean_html

SCHEMA = {
    "type": "function",
    "function": {
        "name": "clean_and_load",
        "description": (
            "Read an HTML 10-K from disk, clean it to plain text, and store the text "
            "in session state. Returns a text_id that downstream tools (find_anchors, "
            "slice_items, read_chars) use to refer to this cleaned text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "html_path": {
                    "type": "string",
                    "description": "Absolute path to the HTML file.",
                },
                "extra_strip_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of regex patterns to strip after default cleanup. "
                        "Use this to remove recurring page-running footers "
                        "(e.g. 'Apple Inc. | 2023 Form 10-K | 17')."
                    ),
                },
            },
            "required": ["html_path"],
        },
    },
}


def run(state, args: dict) -> dict:
    raw = Path(args["html_path"]).read_text(encoding="utf-8", errors="replace")
    text = clean_html(raw, extra_strip_patterns=args.get("extra_strip_patterns"))
    text_id = state.store_text(text)
    return {"text_id": text_id, "length": len(text), "n_lines": text.count("\n") + 1}
