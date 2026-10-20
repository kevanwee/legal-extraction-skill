"""Source-span extraction records: every extracted value bound to exact text in a saved source.

Standard library only. Commands:
    init RUN --fields FIELDS.json
    add-source RUN FILE --id D1 --title TITLE [--text TEXT_FILE --transformation HOW] [--reference WHERE]
    record RUN --field NAME --status found|not-found|ambiguous [--value V] [--quote SRC "words" ...]
           [--occurrence N] [--note TEXT]
    check RUN
    export RUN

Offsets are zero-based Python string offsets into the saved UTF-8 text, end exclusive.
Exit codes: 0 ok, 1 check failed, 2 usage or input error.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
FIELD = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
STATUSES = ("found", "not-found", "ambiguous")
STANDARD_LIMIT = ("A passing span check shows each value is quoted from the saved source at the recorded "
                  "offsets; it does not show that the right passage was chosen or that the value is legally correct.")


class UsageError(Exception):
    pass


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def collapse(text):
    return re.sub(r"\s+", " ", text).strip()


def load(run):
    path = Path(run) / "run.json"
    if not path.is_file():
        raise UsageError(f"No run.json in {run}; run init first")
    return json.loads(path.read_text(encoding="utf-8"))


def save(run, data):
    (Path(run) / "run.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def source_text(run, source):
    return (Path(run) / source["text"]).read_text(encoding="utf-8")


def locate(text, words):
    """All spans of `words` in `text`, ignoring differences in whitespace only."""
    normal, index = [], []
    previous_space = True
    for position, char in enumerate(text):
        if char.isspace():
            if not previous_space:
                normal.append(" ")
                index.append(position)
            previous_space = True
        else:
            normal.append(char)
            index.append(position)
            previous_space = False
    haystack, needle = "".join(normal), collapse(words)
    if not needle:
        raise UsageError("Quoted words are empty")
    spans, start = [], haystack.find(needle)
    while start != -1:
        first, last = index[start], index[start + len(needle) - 1]
        spans.append((first, last + 1))
        start = haystack.find(needle, start + 1)
    return spans


def cmd_init(args):
    run = Path(args.run)
    if (run / "run.json").exists():
        raise UsageError("run.json already exists; use a fresh run folder")
    fields = json.loads(Path(args.fields).read_text(encoding="utf-8"))
    if not isinstance(fields, list) or not fields:
        raise UsageError("Fields file must be a non-empty JSON list")
    names = set()
    for field in fields:
        if not isinstance(field, dict) or not FIELD.fullmatch(str(field.get("name", ""))) or not field.get("description"):
            raise UsageError("Each field needs a lower_snake name and a description")
        if field["name"] in names:
            raise UsageError(f"Duplicate field {field['name']}")
        names.add(field["name"])
    run.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": 1, "run_id": uuid.uuid4().hex,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fields": [{"name": f["name"], "description": f["description"], "multiple": bool(f.get("multiple", False))}
                       for f in fields],
            "sources": [], "values": [], "gaps": [], "review": ""}
    save(run, data)
    return {"status": "initialised", "run_id": data["run_id"], "fields": len(fields)}


def cmd_add_source(args):
    run, data = Path(args.run), load(args.run)
    if not ID.fullmatch(args.id) or any(s["id"] == args.id for s in data["sources"]):
        raise UsageError("Source id must be unique and start with a letter")
    original = Path(args.file)
    if not original.is_file():
        raise UsageError(f"No such file: {original}")
    folder = run / "sources" / args.id
    folder.mkdir(parents=True, exist_ok=False)
    kept = folder / ("original" + original.suffix)
    shutil.copyfile(original, kept)
    if args.text:
        if not args.transformation:
            raise UsageError("--text needs --transformation describing how the text was produced")
        text_path = folder / "text.txt"
        shutil.copyfile(args.text, text_path)
    else:
        try:
            kept.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise UsageError("Original is not UTF-8 text; supply --text and --transformation") from exc
        text_path = kept
    record = {"id": args.id, "title": args.title, "reference": args.reference or str(original),
              "original": kept.relative_to(run).as_posix(), "original_sha256": sha256(kept),
              "text": text_path.relative_to(run).as_posix(), "text_sha256": sha256(text_path),
              "transformation": args.transformation or "none: original is UTF-8 text"}
    data["sources"].append(record)
    save(run, data)
    return {"status": "added", "source": record}


def cmd_record(args):
    data = load(args.run)
    field = next((f for f in data["fields"] if f["name"] == args.field), None)
    if field is None:
        raise UsageError(f"Unknown field {args.field}; declared fields: {[f['name'] for f in data['fields']]}")
    spans = []
    for source_id, words in args.quote or []:
        source = next((s for s in data["sources"] if s["id"] == source_id), None)
        if source is None:
            raise UsageError(f"Unknown source {source_id}")
        text = source_text(args.run, source)
        found = locate(text, words)
        if not found:
            raise UsageError(f"Words not found in {source_id}: {words!r}")
        if len(found) > 1 and args.occurrence is None:
            raise UsageError(f"Words occur {len(found)} times in {source_id}; choose one with --occurrence (1-{len(found)})")
        pick = found[(args.occurrence or 1) - 1] if args.occurrence is None or 1 <= args.occurrence <= len(found) else None
        if pick is None:
            raise UsageError(f"--occurrence must be between 1 and {len(found)}")
        spans.append({"doc_id": source_id, "start": pick[0], "end": pick[1], "text": text[pick[0]:pick[1]]})
    entry = {"field": args.field, "status": args.status,
             "value": None if args.status == "not-found" else args.value,
             "spans": spans, "note": args.note or ""}
    if not field["multiple"]:
        data["values"] = [v for v in data["values"] if v["field"] != args.field]
    data["values"].append(entry)
    save(args.run, data)
    return {"status": "recorded", "entry": entry}


def run_check(run, data):
    errors, warnings = [], []
    sources = {s["id"]: s for s in data["sources"]}
    texts = {}
    for source in data["sources"]:
        for key in ("original", "text"):
            path = Path(run) / source[key]
            if not path.is_file():
                errors.append(f"{source['id']}: {key} file missing")
            elif sha256(path) != source[key + "_sha256"]:
                errors.append(f"{source['id']}: {key} file changed since it was added")
        if (Path(run) / source["text"]).is_file():
            texts[source["id"]] = source_text(run, source)
    declared = {f["name"]: f for f in data["fields"]}
    for name, field in declared.items():
        entries = [v for v in data["values"] if v["field"] == name]
        if not entries:
            errors.append(f"{name}: not addressed; record found, not-found or ambiguous")
        elif not field["multiple"] and len(entries) > 1:
            errors.append(f"{name}: single-value field has {len(entries)} entries")
    for number, value in enumerate(data["values"], 1):
        label = f"value {number} ({value['field']})"
        if value["field"] not in declared:
            errors.append(f"{label}: field is not declared")
        status, spans, note = value["status"], value["spans"], value.get("note", "").strip()
        if status not in STATUSES:
            errors.append(f"{label}: unknown status {status}")
        if status == "found":
            if not value.get("value"):
                errors.append(f"{label}: found needs a value")
            if not spans:
                errors.append(f"{label}: found needs at least one span")
        if status == "not-found":
            if value.get("value") is not None or spans:
                errors.append(f"{label}: not-found must have no value and no spans")
            if not note:
                errors.append(f"{label}: not-found needs a note saying what was searched")
        if status == "ambiguous" and (len(spans) < 2 or not note):
            errors.append(f"{label}: ambiguous needs at least two spans and a note")
        for span in spans:
            text = texts.get(span["doc_id"])
            if span["doc_id"] not in sources:
                errors.append(f"{label}: span cites unknown source {span['doc_id']}")
            elif text is not None:
                if not (0 <= span["start"] < span["end"] <= len(text)):
                    errors.append(f"{label}: span offsets out of range in {span['doc_id']}")
                elif text[span["start"]:span["end"]] != span["text"]:
                    errors.append(f"{label}: span text does not match {span['doc_id']} at {span['start']}-{span['end']}")
        if status == "found" and value.get("value") and spans:
            quoted = " ".join(collapse(s["text"]).lower() for s in spans)
            if collapse(value["value"]).lower() not in quoted and not note:
                errors.append(f"{label}: value does not appear in its spans; add a note explaining the derivation")
    if not data["review"].strip():
        warnings.append("review is empty: record who reviewed the extraction and what was rechecked")
    return {"status": "fail" if errors else "pass", "errors": errors, "warnings": warnings,
            "checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_sha256": hashlib.sha256((Path(run) / "run.json").read_bytes()).hexdigest()}


def cmd_check(args):
    data = load(args.run)
    result = run_check(args.run, data)
    (Path(args.run) / "check.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def cmd_export(args):
    data = load(args.run)
    result = run_check(args.run, data)
    limitations = [g for g in data["gaps"] if str(g).strip()] + [STANDARD_LIMIT]
    if result["status"] != "pass":
        limitations.insert(0, "Span check failed: " + "; ".join(result["errors"][:5]))
    values = [{"field": v["field"], "status": v["status"], "value": v["value"],
               "spans": [{k: s[k] for k in ("doc_id", "start", "end", "text")} for s in v["spans"]],
               "note": v.get("note", "")} for v in data["values"]]
    return {"values": values, "check": {"status": result["status"], "record": str(Path(args.run).resolve())},
            "limitations": limitations}


def parser():
    p = argparse.ArgumentParser(prog="spans.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("init")
    s.add_argument("run")
    s.add_argument("--fields", required=True)
    s = sub.add_parser("add-source")
    s.add_argument("run")
    s.add_argument("file")
    s.add_argument("--id", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--text")
    s.add_argument("--transformation")
    s.add_argument("--reference")
    s = sub.add_parser("record")
    s.add_argument("run")
    s.add_argument("--field", required=True)
    s.add_argument("--status", required=True, choices=STATUSES)
    s.add_argument("--value")
    s.add_argument("--quote", nargs=2, action="append", metavar=("SOURCE", "WORDS"))
    s.add_argument("--occurrence", type=int)
    s.add_argument("--note")
    for name in ("check", "export"):
        sub.add_parser(name).add_argument("run")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    handlers = {"init": cmd_init, "add-source": cmd_add_source, "record": cmd_record, "check": cmd_check, "export": cmd_export}
    try:
        result = handlers[args.command](args)
    except (UsageError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if args.command == "check" and result["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
