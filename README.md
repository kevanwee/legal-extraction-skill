# legal-extraction-skill

An agent skill for extracting defined fields from legal documents, where every value is bound
to the exact characters it came from in a saved, hashed copy of the source. Works with Codex
and Claude Code. The helper is standard-library Python, so it runs anywhere Python 3.11+ does.

```
$ python skills/source-span-extraction/scripts/spans.py record runs/nda --field governing_law \
    --status found --value Singapore --quote D1 "governed by the laws of Singapore"

$ python skills/source-span-extraction/scripts/spans.py check runs/nda
{ "status": "pass", "errors": [], ... }
```

## Why

Extraction is usually the step nobody checks. A model reads forty pages and returns
"Governing law: Singapore", and a reviewer either trusts it or re-reads the forty pages. This
skill makes the check cheap: every value carries the quote and offsets it came from, and a
deterministic check refuses anything that cannot be traced.

## What it guarantees

| The check fails when | Because |
|---|---|
| A quoted span does not match the saved source at its offsets | The value no longer traces to the document |
| A source file changed after it was added | The evidence changed under the record |
| A found value has no span | Nothing supports it |
| A value is not in its quote and has no note | A derived value must say how it was derived |
| A not-found field has no note of what was searched | Absence is a finding, not a blank |
| An ambiguous field quotes fewer than two passages | Competing passages must all be shown |
| A declared field was not addressed | Coverage is part of the result |

Quotes are located ignoring differences in whitespace only, so a quote can cross a line break
in the source while the record keeps the source's exact text. Words that occur more than once
must be chosen explicitly.

A passing check shows that values are quoted accurately. It does not show that the right
passage was chosen, that a document is complete or authentic, or what a clause means in law.

## Install

Copy `skills/source-span-extraction` into your project's `.claude/skills/` or `.agents/skills/`,
or install it with the [legal engineering toolkit](https://github.com/kevanwee/legal-engineering-manifesto)
launcher, which pins this repository and binds the skill to its `extraction@1` contract.

## Use

See [SKILL.md](skills/source-span-extraction/SKILL.md). In short: `init` a private run with
the fields to extract, `add-source` each document (with a recorded text transformation for
PDF, Word or HTML), `record` each field, then `check` and `export`.

## Related

For specialised extraction with their own schemas, see
[chronology](https://github.com/kevanwee/chronology) (litigation events with pincites) and
[oblig-register](https://github.com/kevanwee/oblig-register) (contract obligations with
deadlines). This skill covers general field extraction where no specialised schema exists.

## Test

```
python -m unittest discover -s tests -v
```

## Licence

MIT. See [LICENSE](LICENSE).
