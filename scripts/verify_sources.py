"""T-02 — the policy corpus, hashed, and three answers that carry spans.

    python scripts/verify_sources.py            verify. Hits the network.
    python scripts/verify_sources.py --fetch    download, extract, write artifacts.
    python scripts/verify_sources.py --offline  verify everything except the
                                                re-download. Does NOT close T-02.

Three documents: two per D21, one per D29. NCD 100.1 says what Medicare covers
nationally and quantifies nothing; Article A53028 supplies every constant the
criteria tree needs and is published by one MAC, not by CMS. Both are stored as
extracted text because the MCD emits a fresh CSP nonce per response and raw HTML
therefore has no reproducible hash. R931CP is the 2006 claims-processing
transmittal that binds procedure names to HCPCS codes -- citable for code
bindings and for nothing else, because its coverage content predates the 2012
LSG delegation (D29). It is a static PDF, stored as pypdf-extracted text with
the raw PDF hash recorded alongside.

Nothing here calls a model. The answers were read by a human from the source and
are checked mechanically by slicing the document (Article III).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "data" / "policies" / "source"
MANIFEST_PATH = SOURCE_DIR / "sources.json"
ANSWERS_PATH = SOURCE_DIR / "answers.json"

# Bumping this changes every offset in answers.json. It is recorded in the
# manifest so a changed extractor fails the gate instead of silently re-anchoring
# every citation in the project (D21).
EXTRACTOR_VERSION = 1

# The PDF path is hostage to the exact pypdf version: a different release may
# extract different text, which the re-download check would misreport as the
# published document changing. The manifest records this string per PDF
# document, and verification refuses to run under any other version (D29).
PDF_EXTRACTOR = "pypdf 6.18.0"

USER_AGENT = "Prior-Authorization-Agent/0.1 (T-02 policy source verification)"
TIMEOUT_SECONDS = 60


# --------------------------------------------------------------------------
# The corpus (D21)
# --------------------------------------------------------------------------

DOCUMENTS: list[dict[str, str]] = [
    {
        "document_id": "ncd_100_1",
        "title": "NCD 100.1 - Bariatric Surgery for Treatment of Co-Morbid "
                 "Conditions Related to Morbid Obesity",
        "url": "https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?ncdid=57",
        "authority": "national",
        "publisher": "Centers for Medicare & Medicaid Services",
        "filename": "ncd_100_1.txt",
    },
    {
        "document_id": "a53028",
        "title": "Article A53028 - Billing and Coding: Bariatric Surgery Coverage",
        "url": "https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleid=53028",
        # Not national. Every operational constant in the criteria tree comes
        # from here, so the system determines coverage as this MAC would.
        "authority": "mac_jurisdiction_f",
        "publisher": "Noridian Healthcare Solutions, LLC (A/B MAC, Jurisdiction F)",
        "filename": "a53028.txt",
    },
    {
        "document_id": "r931cp",
        "title": "CMS Pub. 100-04 Transmittal 931 (CR 5013) - Billing Requirements "
                 "for Bariatric Surgery for Treatment of Morbid Obesity",
        "url": "https://www.cms.gov/Regulations-and-Guidance/Guidance/Transmittals/"
               "downloads/R931CP.pdf",
        "authority": "national",
        "publisher": "Centers for Medicare & Medicaid Services",
        "filename": "r931cp.txt",
        "format": "pdf",
        # D29's scope rule. This document binds procedure names to codes and is
        # citable for that alone; its coverage statements predate the 2012 LSG
        # delegation and are stale. A coverage claim spanned here is a defect.
        "scope": "code_bindings_only",
    },
    {
        "document_id": "l34576",
        "title": "LCD L34576 - Laparoscopic Sleeve Gastrectomy for Severe Obesity",
        "url": "https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?LCDId=34576",
        # The second jurisdiction (T-87, D97, D101). Every quantified constant
        # in the Palmetto tree comes from here; a different MAC is a different
        # tree over the same NCD (D21).
        "authority": "mac_jurisdiction_jj_jm",
        "publisher": "Palmetto GBA (A/B MAC, Jurisdictions J and M)",
        "filename": "l34576.txt",
    },
    {
        "document_id": "a56852",
        "title": "Article A56852 - Billing and Coding: Laparoscopic Sleeve "
                 "Gastrectomy for Severe Obesity",
        "url": "https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleid=56852",
        "authority": "mac_jurisdiction_jj_jm",
        "publisher": "Palmetto GBA (A/B MAC, Jurisdictions J and M)",
        "filename": "a56852.txt",
        # The CPT table sits behind the AMA licence modal, outside the
        # `document-view-section` containers this extractor reads, so the
        # extracted text names no code. Hashed and kept so an extractor that
        # reads the licensed table can cite it later; the Palmetto tree's code
        # bindings cite the corpus sentence that names the code (D28, D101).
        "note": "extracted text carries no CPT codes; see D101",
    },
]


# --------------------------------------------------------------------------
# The three answers T-02 owes, read from the source by a human.
#
# `quote` is the claim. The offsets are not written here -- `--fetch` locates
# each quote in its document and refuses any quote that does not appear exactly
# once, so a paraphrase cannot become a citation.
# --------------------------------------------------------------------------

ANSWERS: list[dict[str, Any]] = [
    {
        "question_id": "q1",
        "question": "Does A53028 require diet and activity documentation monthly or once?",
        "feeds": "c5 documentation_rate",
        "answer": "monthly",
        "document_id": "a53028",
        "quote": "The weight-management program must include monthly documentation of "
                 "patient’s weight and BMI, current dietary regimen and physical "
                 "activity (e.g. exercise program).",
        "note": "Monthly, and over the same run c3 identifies. The same sentence is "
                "the source for c4 (BMI documented) and for c5 covering diet and "
                "activity together rather than separately.",
    },
    {
        "question_id": "q2",
        "question": "Is c2's recency window 12 months?",
        "feeds": "c2 recency window",
        "answer": "12 months",
        "document_id": "a53028",
        "quote": "active participation within the last 12 months prior to bariatric "
                 "surgery in a weight-management program that is supervised by a "
                 "physician or other health care professionals for a minimum of four "
                 "consecutive months",
        "note": "Confirms E5's label of 12. The same sentence also fixes c3's "
                "qualifying run at four consecutive months, which is what E4 is "
                "labeled against.",
    },
    {
        "question_id": "q3",
        "question": "Is CPT 43775 (laparoscopic sleeve gastrectomy) nationally covered?",
        "feeds": "sc1 / REQ-2, and T-25's demo case",
        "answer": "no - not nationally covered, and not nationally non-covered either; "
                  "delegated to the MACs on and after 2012-06-27, and this MAC covers it",
        "document_id": "ncd_100_1",
        "quote": "Effective for services performed on and after June 27, 2012, Medicare "
                 "Administrative Contractors (MACs) acting within their respective "
                 "jurisdictions may determine coverage of stand-alone laparoscopic "
                 "sleeve gastrectomy (LSG) for the treatment of co-morbid conditions "
                 "related to obesity in Medicare beneficiaries",
        "note": "D22. The nationally non-covered entry for LSG is scoped 'prior to "
                "June 27, 2012'. A53028 records this MAC exercising the discretion, so "
                "under this corpus 43775 is covered and runs the full tree. T-25 and E3 "
                "assume the opposite; T-35 and T-36 carry the consequence.",
        "corroborating_quote": {
            "document_id": "a53028",
            "quote": "This article is revised to include contractor determined coverage "
                     "for laparoscopic sleeve gastrectomy (43775).",
        },
    },
    # T-87 (D101): the Palmetto tree's constants, read from L34576 the way
    # q1-q3 were read from A53028.
    {
        "question_id": "q4",
        "question": "Does L34576 state a minimum run length for the weight-management "
                    "program, and what recency window does it state?",
        "feeds": "Palmetto c2 recency window; the absence of a Palmetto c3",
        "answer": "12 months; no run length is stated anywhere in the document",
        "document_id": "l34576",
        "quote": "Active participation within the last 12 months prior to bariatric "
                 "surgery in a weight-management program that is supervised by a "
                 "physician or other health care professionals.",
        "note": "The sentence A53028 continues with 'for a minimum of four consecutive "
                "months' ends here. The word 'consecutive' does not occur in L34576, "
                "so the Palmetto tree declares no c3 (D101).",
    },
    {
        "question_id": "q5",
        "question": "What must L34576's program document monthly?",
        "feeds": "Palmetto c4 and c5 documentation_rate; c4 is unclaimed",
        "answer": "weight, current dietary regimen and physical activity, monthly",
        "document_id": "l34576",
        "quote": "The weight-management program must include monthly documentation of "
                 "ALL of the following components:\n\nweight\n\ncurrent dietary "
                 "regimen\n\nphysical activity (e.g., exercise program)",
        "note": "Weight, not BMI: A53028 says 'weight and BMI'. The extractor reads a "
                "documented BMI and has no weight field, which is why the Palmetto "
                "c4 is declared unclaimed rather than evaluated on a proxy (D101).",
    },
    {
        "question_id": "q6",
        "question": "Does L34576 require an evaluation the pipeline has no extractor for?",
        "feeds": "Palmetto d, declared unclaimed",
        "answer": "yes - a multidisciplinary evaluation within the previous 6 months",
        "document_id": "l34576",
        "quote": "A thorough multidisciplinary evaluation within the previous 6 months "
                 "which includes ALL of the following:",
        "note": "Four named components follow (surgeon, primary care referral, mental "
                "health, nutrition). Declared in the tree with its window spanned and "
                "abstained on with NOT_EVALUATED_BY_THIS_SYSTEM (D101).",
    },
]


# --------------------------------------------------------------------------
# Extraction. Structural, deterministic, stdlib only (D21).
# --------------------------------------------------------------------------


class _SectionText(HTMLParser):
    """Collect text inside the MCD's `document-view-section` containers.

    Anchoring on that class rather than on the whole page is what drops the
    navigation, the modals and the script tags -- the parts of the response that
    carry the per-request nonce and would make the hash unreproducible.
    """

    SKIP_TAGS = {"script", "style", "noscript"}
    BREAK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.skipping = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}
        if self.depth and tag in self.SKIP_TAGS:
            self.skipping += 1
            return
        if self.depth:
            self.depth += 1
            if tag in self.BREAK_TAGS:
                self.chunks.append("\n")
            return
        if "document-view-section" in attr.get("class", "") and attr.get("id"):
            self.depth = 1
            self.chunks.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skipping and tag in self.SKIP_TAGS:
            self.skipping -= 1
            return
        if self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth and not self.skipping:
            self.chunks.append(data)


def extract_text(html: str) -> str:
    """HTML to the plain text every span in this project anchors into."""
    parser = _SectionText()
    parser.feed(html)
    text = "".join(parser.chunks)
    # Normalize newlines and the non-breaking spaces the MCD uses for layout,
    # then collapse runs. Every step is unconditional -- nothing here depends on
    # the content, so the same page always yields the same bytes.
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text + "\n"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def download_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def download(url: str) -> str:
    return download_bytes(url).decode("utf-8", errors="strict")


def _require_pinned_pypdf() -> "object":
    """Import pypdf, refusing any version but the one the manifest records.

    A different pypdf may extract different text from the same bytes. Failing
    here names the actual problem; letting it through would surface later as
    "the published document changed," which trains someone to re-fetch and
    silently re-anchor every span into the PDF (D29).
    """
    import pypdf

    installed = f"pypdf {pypdf.__version__}"
    if installed != PDF_EXTRACTOR:
        raise SystemExit(
            f"FAIL  {installed} is installed but the PDF corpus was extracted by "
            f"{PDF_EXTRACTOR}. Install the pinned version (requirements.txt) "
            "rather than re-fetching under a new one."
        )
    return pypdf


def extract_pdf_text(data: bytes) -> str:
    """PDF bytes to plain text, one page per line-joined block, pypdf pinned."""
    import io

    pypdf = _require_pinned_pypdf()
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() for page in reader.pages)


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------


def locate(text: str, quote: str, where: str) -> tuple[int, int]:
    """Offsets of `quote` in `text`, demanding exactly one exact occurrence.

    No whitespace normalization and no fuzzy fallback, unlike D18's anchoring of
    model output. These quotes were typed by a human against the extracted text;
    a miss means the answer is wrong or the document moved, and both are things
    the gate should refuse rather than repair.
    """
    count = text.count(quote)
    if count != 1:
        raise SystemExit(
            f"FAIL  {where}: quote occurs {count} times in {where.split()[0]}; "
            "exactly one exact occurrence is required.\n"
            f"      {quote[:100]!r}"
        )
    start = text.find(quote)
    return start, start + len(quote)


def fetch(only: set[str] | None = None) -> int:
    """Download and extract the corpus, or with `only` just the named documents.

    `--only` exists because a re-download of a document already in the corpus
    is a re-measurement of every span into it: the MCD revises articles, and a
    changed extraction would re-anchor T-01's tree, T-15's recording and every
    committed citation at once. Adding a document must not risk that, so the
    documents not named are kept as committed — their text is read from disk
    and their manifest record carried forward unchanged (T-87, D101).
    """
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    texts: dict[str, str] = {}
    records: list[dict[str, Any]] = []
    existing: dict[str, dict[str, Any]] = {}
    if only is not None:
        unknown = only - {d["document_id"] for d in DOCUMENTS}
        if unknown:
            print(f"FAIL  --only names documents not in DOCUMENTS: {sorted(unknown)}",
                  file=sys.stderr)
            return 1
        if not MANIFEST_PATH.is_file():
            print("FAIL  --only needs an existing manifest to carry the others forward",
                  file=sys.stderr)
            return 1
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        existing = {d["document_id"]: d for d in manifest["documents"]}

    for doc in DOCUMENTS:
        if only is not None and doc["document_id"] not in only:
            record = existing.get(doc["document_id"])
            path = SOURCE_DIR / doc["filename"]
            if record is None or not path.is_file():
                print(f"FAIL  {doc['document_id']} is not in the committed corpus; "
                      "name it in --only or run a full --fetch", file=sys.stderr)
                return 1
            texts[doc["document_id"]] = path.read_text(encoding="utf-8")
            records.append(record)
            print(f"  keeping  {doc['document_id']} as committed, sha256 "
                  f"{record['sha256'][:12]}")
            continue
        print(f"  fetching {doc['document_id']} ... ", end="", flush=True)
        try:
            raw = download_bytes(doc["url"])
        except (urllib.error.URLError, TimeoutError) as exc:
            print("FAILED")
            print(f"FAIL  {doc['document_id']}: {exc}", file=sys.stderr)
            return 1
        record: dict[str, Any] = {
            **doc,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "extractor_version": EXTRACTOR_VERSION,
        }
        if doc.get("format") == "pdf":
            text = extract_pdf_text(raw)
            record["extractor"] = PDF_EXTRACTOR
            record["pdf_sha256"] = hashlib.sha256(raw).hexdigest()
        else:
            text = extract_text(raw.decode("utf-8", errors="strict"))
        record["sha256"] = sha256(text)
        record["char_count"] = len(text)
        (SOURCE_DIR / doc["filename"]).write_text(text, encoding="utf-8")
        texts[doc["document_id"]] = text
        records.append(record)
        print(f"{len(text)} chars, sha256 {sha256(text)[:12]}")

    MANIFEST_PATH.write_text(
        json.dumps({"extractor_version": EXTRACTOR_VERSION, "documents": records}, indent=2)
        + "\n",
        encoding="utf-8",
    )

    resolved: list[dict[str, Any]] = []
    for answer in ANSWERS:
        entry = dict(answer)
        doc_id = entry["document_id"]
        start, end = locate(texts[doc_id], entry["quote"], f"{doc_id} {entry['question_id']}")
        entry["char_start"], entry["char_end"] = start, end
        if "corroborating_quote" in entry:
            corroborating = dict(entry["corroborating_quote"])
            c_id = corroborating["document_id"]
            c_start, c_end = locate(
                texts[c_id], corroborating["quote"], f"{c_id} {entry['question_id']} corroborating"
            )
            corroborating["char_start"], corroborating["char_end"] = c_start, c_end
            entry["corroborating_quote"] = corroborating
        resolved.append(entry)
        print(f"  {entry['question_id']}: {doc_id}[{start}:{end}] -> {entry['answer']}")

    ANSWERS_PATH.write_text(
        json.dumps(
            {
                "resolves": "spec section 9 open questions 1 and 2, and T-25's coverage "
                            "assumption for 43775",
                "answered_at": datetime.now(timezone.utc).isoformat(),
                "answers": resolved,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nWrote {MANIFEST_PATH.relative_to(REPO_ROOT)} and "
          f"{ANSWERS_PATH.relative_to(REPO_ROOT)}. Now run without --fetch.")
    return 0


# --------------------------------------------------------------------------
# Verify — T-02's exit condition
# --------------------------------------------------------------------------


def verify(offline: bool) -> int:
    checks: list[str] = []
    failures: list[str] = []

    def ok(msg: str) -> None:
        checks.append(msg)

    def bad(msg: str) -> None:
        failures.append(msg)

    for path in (MANIFEST_PATH, ANSWERS_PATH):
        if not path.exists():
            print(
                f"FAIL  {path.relative_to(REPO_ROOT)} is absent. Run with --fetch first.",
                file=sys.stderr,
            )
            return 1

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    answers = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))
    records = {d["document_id"]: d for d in manifest["documents"]}
    texts: dict[str, str] = {}

    # 1. The corpus D21 names is the corpus on disk.
    expected = {d["document_id"] for d in DOCUMENTS}
    if set(records) != expected:
        bad(f"manifest documents {sorted(records)} do not match the corpus {sorted(expected)}")
    if manifest.get("extractor_version") != EXTRACTOR_VERSION:
        bad(
            f"manifest was written by extractor version "
            f"{manifest.get('extractor_version')}, this is {EXTRACTOR_VERSION}; "
            "every recorded offset is suspect. Re-fetch."
        )

    # 2. Present, and hashing to what the manifest recorded.
    for doc_id, record in sorted(records.items()):
        path = SOURCE_DIR / record["filename"]
        if not path.is_file():
            bad(f"{doc_id}: {record['filename']} is absent")
            continue
        text = path.read_text(encoding="utf-8")
        texts[doc_id] = text
        if sha256(text) != record["sha256"]:
            bad(f"{doc_id}: on-disk content does not match its recorded hash")
        elif len(text) != record["char_count"]:
            bad(f"{doc_id}: char_count {record['char_count']} but file holds {len(text)}")
        else:
            ok(f"{doc_id} present, {len(text)} chars, hash matches ({record['authority']})")

    # 3. Re-downloadable to the same hash. The whole point of the exit
    #    condition: a policy document that changed under us must fail here
    #    rather than quietly re-anchor every citation in the project.
    if offline:
        ok("re-download SKIPPED (--offline); this run does not close T-02")
    else:
        for doc_id, record in sorted(records.items()):
            try:
                raw = download_bytes(record["url"])
            except (urllib.error.URLError, TimeoutError) as exc:
                bad(f"{doc_id}: re-download failed: {exc}")
                continue
            if record.get("format") == "pdf":
                if hashlib.sha256(raw).hexdigest() != record["pdf_sha256"]:
                    bad(
                        f"{doc_id}: the published PDF's bytes changed upstream; "
                        "the extracted text and every span into it are suspect."
                    )
                    continue
                fresh = extract_pdf_text(raw)
            else:
                fresh = extract_text(raw.decode("utf-8", errors="strict"))
            if sha256(fresh) != record["sha256"]:
                bad(
                    f"{doc_id}: re-download extracts to a different hash. Either the "
                    "published policy changed or extraction is not deterministic; "
                    "both invalidate every span recorded against it."
                )
            else:
                ok(f"{doc_id} re-downloaded and re-extracted to the same hash")

    # 4. Three answers, each carrying a span that slices back to its quote.
    #    Article III applied to policy constants: a criteria-tree number traces
    #    to source text the same way a determination's claims do.
    seen: set[str] = set()
    for answer in answers["answers"]:
        qid = answer["question_id"]
        seen.add(qid)
        if not answer.get("answer"):
            bad(f"{qid}: no answer recorded")
        cites = [answer] + ([answer["corroborating_quote"]] if "corroborating_quote" in answer else [])
        for cite in cites:
            doc_id = cite["document_id"]
            text = texts.get(doc_id)
            if text is None:
                bad(f"{qid}: cites {doc_id}, which is not in the corpus")
                continue
            start, end = cite.get("char_start"), cite.get("char_end")
            if not isinstance(start, int) or not isinstance(end, int):
                bad(f"{qid}: citation into {doc_id} carries no (char_start, char_end)")
                continue
            if not 0 <= start < end <= len(text):
                bad(f"{qid}: span [{start}:{end}] is out of range for {doc_id}")
                continue
            if text[start:end] != cite["quote"]:
                bad(f"{qid}: {doc_id}[{start}:{end}] does not slice back to its quote")

    missing = {a["question_id"] for a in ANSWERS} - seen
    if missing:
        bad(f"answers.json is missing {sorted(missing)}")
    elif not failures:
        ok(f"{len(seen)} answers, each sliced from its document at the recorded offsets")

    for line in checks:
        print(f"ok    {line}")
    for line in failures:
        print(f"FAIL  {line}", file=sys.stderr)

    if failures:
        print(f"\n{len(failures)} check(s) failed.", file=sys.stderr)
        return 1

    print("\nT-02 sources verified.")
    for answer in answers["answers"]:
        print(f"  {answer['question_id']}  {answer['question']}")
        print(f"        {answer['answer']}")
        print(f"        {answer['document_id']}"
              f"[{answer['char_start']}:{answer['char_end']}]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetch", action="store_true", help="download, extract and write the artifacts"
    )
    parser.add_argument(
        "--only",
        default=None,
        help="with --fetch: comma-separated document ids to download; every other "
             "document is carried forward as committed (T-87)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip the re-download check. Does not close T-02.",
    )
    args = parser.parse_args()
    if args.only and not args.fetch:
        parser.error("--only only means something with --fetch")
    only = {d.strip() for d in args.only.split(",") if d.strip()} if args.only else None
    return fetch(only) if args.fetch else verify(args.offline)


if __name__ == "__main__":
    raise SystemExit(main())
