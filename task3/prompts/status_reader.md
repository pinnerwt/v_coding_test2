You are reading a single, short slice of a US SEC Form 10-K filing. The slice
covers exactly one numbered Item (e.g. "Item 11"). Decide which of four
functional categories the slice falls into, and reply with ONE label and
nothing else.

The four labels:

- `substantive` — the slice contains actual disclosure: facts, figures,
  narrative descriptions, or any text that itself is the disclosure being
  required. Even a brief paragraph that answers the Item directly counts.
- `incorporated_by_reference` — the slice tells the reader that the required
  information is provided elsewhere (a proxy statement, an exhibit, another
  Item) rather than restating it. The slice itself does not contain the
  disclosure; it just points at where the disclosure lives.
- `not_applicable` — the registrant declares the Item does not apply to them
  this period, or there is nothing to report. Examples: "None.", "Not
  applicable.", "There were no reportable events."
- `reserved` — the Item itself is officially a placeholder under SEC rules
  (e.g. post-2020 Item 6 "[Reserved]"). The slice's content is essentially
  just "[Reserved]" or an equivalent placeholder.

Read the slice as a human filings reader would. Don't pattern-match keywords
mechanically — judge the function: is this the disclosure, a pointer to it,
a declaration of absence, or an SEC-mandated placeholder?

Reply with exactly one of: `substantive`, `incorporated_by_reference`,
`not_applicable`, `reserved`. Nothing else.

---

SLICE:

{slice_text}
