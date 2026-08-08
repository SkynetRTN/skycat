# Decision records

Choices that are settled, with the reasoning that settled them. The point of
writing them down is that the next person to ask "why not just…" gets the
argument rather than a re-litigation — and that a decision worth reversing can
be reversed against a record of what it was actually buying.

| # | Decision | Status |
|---|---|---|
| [0001](0001-postgresql-postgis-only.md) | PostgreSQL/PostGIS only — no SQLite, no Python-side spatial fallback | Accepted |

## Writing one

Number sequentially, name the file `NNNN-short-kebab-title.md`, and give it
frontmatter with `status` and `date`. The body follows the shape of 0001:

- **Status** — accepted, superseded (by which record), or proposed.
- **Context** — what the constraint is and what it genuinely costs. Write the
  costs honestly; a record that only lists upsides convinces nobody and gets
  overridden by the first person who hits the downside.
- **Why** — the reasoning, split by the alternative it rules out.
- **What is done instead** — the mitigations that make the cost bearable.
- **Revisiting** — what evidence would justify reopening this, and explicitly
  what would not.

A decision record is not a design doc. If it is describing how something works
rather than why an alternative was rejected, it belongs in
[`../reference/architecture.md`](../reference/architecture.md).
