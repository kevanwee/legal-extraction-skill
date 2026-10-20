"""Behaviour of the span helper: values are only accepted when they trace to exact saved text."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "source-span-extraction" / "scripts" / "spans.py"
CONTRACT = ("SYNTHETIC SUPPLY AGREEMENT\n\n12. Governing law\nThis Agreement is governed by the laws of\n"
            "Singapore.\n\n13. Notices\nNotice may be given by email. Notice is deemed received on the next "
            "Business Day.\n\n14. Term\nThis Agreement commences on 1st January 2026.\n")


class SpanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.run_dir = base / "run"
        (base / "contract.txt").write_text(CONTRACT, encoding="utf-8")
        (base / "fields.json").write_text(json.dumps([
            {"name": "governing_law", "description": "Law governing the agreement"},
            {"name": "commencement_date", "description": "Date the agreement starts"},
            {"name": "arbitration_seat", "description": "Seat of arbitration, if any"},
        ]), encoding="utf-8")
        self.call("init", self.run_dir, "--fields", base / "fields.json")
        self.call("add-source", self.run_dir, base / "contract.txt", "--id", "D1", "--title", "Synthetic supply agreement")

    def call(self, *args, expect=0):
        process = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)
        self.assertEqual(process.returncode, expect, process.stdout + process.stderr)
        return json.loads(process.stdout)

    def record_all(self):
        self.call("record", self.run_dir, "--field", "governing_law", "--status", "found", "--value", "Singapore",
                  "--quote", "D1", "governed by the laws of Singapore")
        self.call("record", self.run_dir, "--field", "commencement_date", "--status", "found", "--value", "2026-01-01",
                  "--quote", "D1", "commences on 1st January 2026", "--note", "Normalised from the written date.")
        self.call("record", self.run_dir, "--field", "arbitration_seat", "--status", "not-found",
                  "--note", "Read clauses 1-14; no arbitration clause.")

    def test_quote_across_a_line_break_binds_exact_offsets(self):
        entry = self.call("record", self.run_dir, "--field", "governing_law", "--status", "found", "--value", "Singapore",
                          "--quote", "D1", "governed by the laws of Singapore")["entry"]
        span = entry["spans"][0]
        self.assertEqual(CONTRACT[span["start"]:span["end"]], span["text"])
        self.assertIn("\n", span["text"])  # the source's own line break is preserved, not normalised away

    def test_complete_record_passes_and_exports_the_contract_shape(self):
        self.record_all()
        self.assertEqual(self.call("check", self.run_dir)["status"], "pass")
        exported = self.call("export", self.run_dir)
        self.assertEqual(exported["check"]["status"], "pass")
        self.assertEqual({v["status"] for v in exported["values"]}, {"found", "not-found"})
        self.assertTrue(exported["limitations"])

    def test_words_absent_from_the_source_are_refused(self):
        error = self.call("record", self.run_dir, "--field", "governing_law", "--status", "found", "--value", "England",
                          "--quote", "D1", "governed by the laws of England", expect=2)
        self.assertIn("not found", error["error"])

    def test_repeated_words_need_an_explicit_occurrence(self):
        error = self.call("record", self.run_dir, "--field", "governing_law", "--status", "found", "--value", "x",
                          "--quote", "D1", "Notice", expect=2)
        self.assertIn("occur 3 times", error["error"])  # "Notices", "Notice may", "Notice is"

    def test_unaddressed_field_and_unexplained_derived_value_fail(self):
        self.call("record", self.run_dir, "--field", "commencement_date", "--status", "found", "--value", "2026-01-01",
                  "--quote", "D1", "commences on 1st January 2026")
        result = self.call("check", self.run_dir, expect=1)
        joined = " ".join(result["errors"])
        self.assertIn("governing_law: not addressed", joined)
        self.assertIn("explaining the derivation", joined)

    def test_changed_source_or_edited_span_fails(self):
        self.record_all()
        record = json.loads((self.run_dir / "run.json").read_text(encoding="utf-8"))
        record["values"][0]["spans"][0]["start"] += 1
        (self.run_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")
        self.assertIn("does not match", " ".join(self.call("check", self.run_dir, expect=1)["errors"]))
        text = next((self.run_dir / "sources" / "D1").iterdir())
        text.write_text(CONTRACT.replace("Singapore", "England"), encoding="utf-8")
        self.assertIn("changed since it was added", " ".join(self.call("check", self.run_dir, expect=1)["errors"]))

    def test_not_found_without_note_and_ambiguous_with_one_span_fail(self):
        self.call("record", self.run_dir, "--field", "arbitration_seat", "--status", "not-found")
        self.call("record", self.run_dir, "--field", "governing_law", "--status", "ambiguous", "--value", "?",
                  "--quote", "D1", "laws of", "--note", "Two candidate clauses.")
        joined = " ".join(self.call("check", self.run_dir, expect=1)["errors"])
        self.assertIn("not-found needs a note", joined)
        self.assertIn("ambiguous needs at least two spans", joined)


if __name__ == "__main__":
    unittest.main()
