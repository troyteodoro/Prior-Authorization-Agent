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

Since T-96 this script verifies **two corpora**, not one. The policy corpus
above is what the system determines coverage from. The second is the
**knowledge corpus** under `data/knowledge/` -- FDA drug labeling, fetched from
DailyMed as SPL XML, which is where `medication_effects.json` quotes its claim
that a drug is known to cause a condition. They are separate manifests on
purpose: "nine policy documents" is a checked claim about what this system
adjudicates against, and five drug labels are not that (D118). One verifier,
one gate, two manifests.

This script owns the **documents** -- present, hashing to what was recorded,
re-downloadable to the same hash. `tests/test_medication_effects.py` owns the
**table** -- its rows, their spans and whether each source resolves into the
manifest. Those are different claims with different evidence, and merging them
is the merge D28 refused.

Nothing here calls a model. The answers were read by a human from the source and
are checked mechanically by slicing the document (Article III).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "data" / "policies" / "source"
MANIFEST_PATH = SOURCE_DIR / "sources.json"
ANSWERS_PATH = SOURCE_DIR / "answers.json"

# The knowledge corpus (T-96, D118). A second directory and a second manifest,
# verified by the same run of this script.
KNOWLEDGE_DIR = REPO_ROOT / "data" / "knowledge" / "source"
KNOWLEDGE_MANIFEST = REPO_ROOT / "data" / "knowledge" / "sources.json"

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

# DailyMed answers a 500 intermittently under no load at all -- measured while
# selecting T-96's labels, on two of six requests. A transient 500 reported as
# "the published document changed" is the diagnosis this script exists to make
# correctly, so 5xx is retried and everything else is raised on the first try.
RETRY_ON_5XX = 4
RETRY_BACKOFF_SECONDS = 2

# The SPL sections a drug-effect claim may be quoted from, by LOINC code. A
# section allowlist is the same move `_SectionText` makes on the MCD: the rest
# of an SPL is packaging, pricing and display panels, which revise without the
# clinical content changing, so quoting only these sections keeps the hash
# tracking the thing the table actually cites (D118).
SPL_SECTIONS: dict[str, str] = {
    "34066-1": "BOXED WARNING",
    "34070-3": "CONTRAINDICATIONS",
    "34071-1": "WARNINGS",
    "42232-9": "PRECAUTIONS",
    "43685-7": "WARNINGS AND PRECAUTIONS",
    "34084-4": "ADVERSE REACTIONS",
}

SPL_URL = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}.xml"


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
    # T-92 (D111): the second practice. Same MAC as the second bariatric
    # jurisdiction -- Palmetto serves J and M for both -- which is the case
    # that tests resolution rather than the one that avoids it.
    {
        "document_id": "l35677",
        "title": "LCD L35677 - Infliximab",
        "url": "https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?LCDId=35677",
        "authority": "mac_jurisdiction_jj_jm",
        "publisher": "Palmetto GBA (A/B MAC, Jurisdictions J and M)",
        "filename": "l35677.txt",
    },
    {
        "document_id": "a56432",
        "title": "Article A56432 - Billing and Coding: Infliximab",
        "url": "https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleid=56432",
        "authority": "mac_jurisdiction_jj_jm",
        "publisher": "Palmetto GBA (A/B MAC, Jurisdictions J and M)",
        "filename": "a56432.txt",
        # The CPT/HCPCS table is behind the AMA licence modal, as A56852's is,
        # so the J-code is not here. The ICD-10-CM codes that support medical
        # necessity *are*, and the rheumatoid arthritis value set anchors into
        # them (D111).
        "note": "extracted text carries no HCPCS codes; the ICD-10-CM groups are present (D111)",
    },
    # T-94 (D114): the third practice, and the first document in this corpus
    # published by neither Noridian nor Palmetto. Its own contractor table
    # lists Alabama alongside the J-5 and J-8 states, so Alabama is served by
    # three trees from three practices -- D111's rule reached from a document
    # rather than designed, and no collision, because 93975 is bound by nobody
    # else.
    {
        "document_id": "l35755",
        "title": "LCD L35755 - Non-Invasive Abdominal / Visceral Vascular Studies",
        "url": "https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?LCDId=35755",
        "authority": "mac_jurisdiction_j5_j8",
        "publisher": "Wisconsin Physicians Service Insurance Corporation "
                     "(A/B MAC, Jurisdictions J-5 and J-8)",
        "filename": "l35755.txt",
    },
    {
        "document_id": "a57591",
        "title": "Article A57591 - Billing and Coding: Non-Invasive Abdominal / "
                 "Visceral Vascular Studies",
        "url": "https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleid=57591",
        "authority": "mac_jurisdiction_j5_j8",
        "publisher": "Wisconsin Physicians Service Insurance Corporation "
                     "(A/B MAC, Jurisdictions J-5 and J-8)",
        "filename": "a57591.txt",
        # The CPT/HCPCS table is behind the AMA licence modal here too, but
        # unlike A56852 and A56432 this article names its codes in prose: each
        # ICD-10 group's paragraph states the procedures the group supports and
        # the codes that denote them. So the code binding cites a coverage
        # document's own sentence rather than a revision-history line (D28,
        # D114).
        "note": "the CPT/HCPCS table is behind the licence modal, but the "
                "ICD-10-CM group paragraphs name 93975/93976, 93978/93979 and "
                "93980/93981 in prose (D114)",
    },
]


# --------------------------------------------------------------------------
# The knowledge corpus (T-96, D118)
#
# FDA labeling, one document per RxNorm ingredient the knowledge table has a
# row for. These are not coverage documents and do not belong to any
# jurisdiction: they are where the claim "this drug is known to cause this
# condition" is quoted from, and nothing else in this repo cites them.
#
# **Which SPL.** DailyMed lists hundreds of SPLs per ingredient, most of them
# repackagers reprinting the same text. The one pinned here is the application
# holder's -- the brand label where one is current -- so the quote traces to
# the originator's text rather than to a repackager's copy. Any FDA-approved
# labeling for the ingredient states the same adverse reactions, because a
# generic's labeling must match the reference listed drug's; the choice is
# about provenance, not content.
#
# A republication upstream makes the online run fail. That is D21's discipline
# working: a label that changed under us must fail here rather than quietly
# re-anchor the table's spans.
# --------------------------------------------------------------------------

DRUG_LABELS: list[dict[str, str]] = [
    {
        "document_id": "spl_prednisone",
        "ingredient": "prednisone",
        "ingredient_rxcui": "8640",
        "title": "DELTASONE (prednisone) tablet - FDA labeling",
        "setid": "d0acd7fb-8401-424f-9acc-a74dcd9b14f6",
        "labeler": "Sonoma Pharmaceuticals, Inc.",
        "filename": "spl_prednisone.txt",
    },
    {
        "document_id": "spl_apixaban",
        "ingredient": "apixaban",
        "ingredient_rxcui": "1364430",
        "title": "ELIQUIS (apixaban) tablet, film coated - FDA labeling",
        "setid": "e9481622-7cc6-418a-acb6-c5450daae9b0",
        "labeler": "E.R. Squibb & Sons, L.L.C.",
        "filename": "spl_apixaban.txt",
    },
    {
        "document_id": "spl_lisinopril",
        "ingredient": "lisinopril",
        "ingredient_rxcui": "29046",
        "title": "ZESTRIL (lisinopril) tablet - FDA labeling",
        "setid": "838c2d78-d2d8-4981-9ec9-e50ef9e1a5d8",
        "labeler": "Upsher-Smith Laboratories, LLC",
        "filename": "spl_lisinopril.txt",
    },
    {
        "document_id": "spl_hydrochlorothiazide",
        "ingredient": "hydrochlorothiazide",
        "ingredient_rxcui": "5487",
        "title": "HYDROCHLOROTHIAZIDE tablet - FDA labeling",
        "setid": "e2270db4-2930-4ec5-ac96-2b4542aed367",
        "labeler": "Teva Pharmaceuticals USA, Inc.",
        "filename": "spl_hydrochlorothiazide.txt",
    },
    {
        "document_id": "spl_methotrexate",
        "ingredient": "methotrexate",
        "ingredient_rxcui": "6851",
        "title": "TREXALL (methotrexate) tablet, film coated - FDA labeling",
        "setid": "e942f8db-510f-44d6-acb5-b822196f5e8c",
        "labeler": "Teva Women's Health LLC",
        "filename": "spl_methotrexate.txt",
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
    # T-92 (D111): the second practice's constants, read from L35677 the way
    # q1-q3 were read from A53028 and q4-q6 from L34576.
    {
        "question_id": "q7",
        "question": "What does L35677 require alongside infliximab for the "
                    "rheumatoid arthritis indication, and does it state a trial "
                    "duration or a screening requirement?",
        "feeds": "infliximab-ra criteria b and f",
        "answer": "concurrent methotrexate; no trial duration and no screening "
                  "requirement are stated for this indication",
        "document_id": "l35677",
        "quote": "When used in combination with methotrexate, to reduce the signs "
                 "and symptoms, inhibit the progression of structural damage and "
                 "improve physical function in patients with moderately to "
                 "severely active rheumatoid arthritis.",
        "note": "The whole RA bullet. 'moderately to severely active' is a clinical "
                "assessment the chart does not grade, so criterion f is unclaimed. "
                "The one quantified trial in this document -- three or more months "
                "of steroids and immunosuppressants -- belongs to the pulmonary "
                "sarcoidosis bullet, not this one, which is why v1.2 row 3's "
                "'NOT_MET on trial duration' was rewritten (D111).",
    },
    {
        "question_id": "q8",
        "question": "What contraindications does L35677 name, and are they in the "
                    "coded record?",
        "feeds": "infliximab-ra criteria c and d, both unclaimed",
        "answer": "Class III or IV congestive heart failure, and untreated active "
                  "or latent tuberculosis; neither is determinable from coded data",
        "document_id": "l35677",
        "quote": "Infliximab will only be covered for the above indications when no "
                 "contraindications to its use exist including:",
        "note": "The two follow as 'a.' and 'b.'. NYHA class is not in ICD-10 -- "
                "I50.x records heart failure and not which class -- and 'untreated' "
                "is a judgment about the record. Both criteria are declared "
                "unclaimed because of the document and the chart, never because "
                "the engine lacks a predicate (REQ-57, D111).",
    },
    {
        "question_id": "q9",
        "question": "What does L35677 exclude, and can it be computed from "
                    "structured data?",
        "feeds": "infliximab-ra criterion e",
        "answer": "combination with another biologic or a Janus kinase inhibitor; "
                  "yes - active medications intersected with a named RxNorm set",
        "document_id": "l35677",
        "quote": "When used in combination with other biologics, such as "
                 "Enbrel\u00ae(etanercept), Kineret\u00ae (anakinra), "
                 "Orencia\u00ae(abatacept), Rituxan\u00ae(rituximab), "
                 "Humira\u00ae(adalimumab), Cimzia\u00ae (certolizumab), "
                 "Simponi\u00ae (golimumab), or a Janus kinase inhibitor "
                 "[e.g. Xeljanz\u00ae (tofacitinib)], infliximab is considered not "
                 "medically reasonable and necessary and therefore, not covered.",
        "note": "The document names eight drugs in prose; the value set carries "
                "each one's RxNorm ingredient and its clinical-drug expansion. An "
                "exclusion concludes from positive evidence: an active medication "
                "in the set is NOT_MET, and none is MET. That is not D40's case, "
                "which is about a requirement a chart cannot prove absent (D111).",
    },
    {
        "question_id": "q10",
        "question": "Does any document in the corpus name the HCPCS code L35677 "
                    "governs?",
        "feeds": "the infliximab-ra tree's contractor_determined binding",
        "answer": "yes - L35677's revision history names J1745; neither document's "
                  "CPT/HCPCS table is in the extracted text",
        "document_id": "l35677",
        "quote": "Under CPT/HCPCS Codes the description was revised for CPT code "
                 "J1745.",
        "note": "A56432's code table sits behind the AMA licence modal, exactly as "
                "A56852's does (D101). This is a revision-history line rather than "
                "a code table, which is weaker evidence and is recorded as such in "
                "the tree: it establishes the code this LCD's group carries, which "
                "is D28's code-binding class of claim. The coverage claim is cited "
                "separately, from the LCD's own coverage sentence (D111).",
    },
    # T-94 (D114): the third practice's constants, read from L35755 and A57591
    # the way q1-q3 were read from A53028, q4-q6 from L34576 and q7-q10 from
    # L35677.
    {
        "question_id": "q11",
        "question": "Does L35755 state a frequency limit for abdominal/visceral "
                    "vascular studies, and does it scope that limit to a place "
                    "of service?",
        "feeds": "us-abdominal-visceral criterion b -- its interval and its "
                 "excluded encounter classes",
        "answer": "once in a year, excluding inpatient hospital (21) and "
                  "emergency room (23) places of service",
        "document_id": "l35755",
        "quote": "Generally, it is expected that noninvasive abdominal/visceral "
                 "vascular studies would not be performed more than once in a "
                 "year, excluding inpatient hospital (21) and emergency room "
                 "(23) places of services.",
        "note": "The one unhedged frequency limit among the four ultrasound "
                "candidates fetched for D114. It is compiled as an interval "
                "rather than a count, because a count would have to answer MET "
                "on a chart with no prior study and REQ-5 refuses a MET with no "
                "span. The two named places of service are dropped from the "
                "arithmetic, read off each prior study's encounter, because "
                "counting an emergency-room study would deny a patient this "
                "document does not restrict (D114).",
    },
    {
        "question_id": "q12",
        "question": "What does L35755 require for medical necessity, and how "
                    "many of its conditions are determinable from coded data?",
        "feeds": "us-abdominal-visceral criteria a, c and d",
        "answer": "three conditions, all of which must be met; only the first "
                  "is a fact in the coded record, and no laboratory value is "
                  "quantified anywhere in the document",
        "document_id": "l35755",
        "quote": "Services are deemed medically necessary when all the "
                 "following conditions are met:\n\n1. Signs/symptoms of "
                 "ischemia or altered blood flow are present;\n2. The "
                 "information is necessary for appropriate medical and/or "
                 "surgical management;\n3. The test is not redundant of other "
                 "diagnostic procedures that must be performed.",
        "note": "Condition 1 is A57591's ICD-10 group, which is criterion (a). "
                "Conditions 2 and 3 are claims about the ordering clinician's "
                "intent and about what else is planned -- neither is a fact in "
                "any record this system reads -- so they are declared unclaimed "
                "and abstained on, never omitted (REQ-58). The answer's second "
                "half is why v1.2's lab-threshold statement stays unminted: the "
                "kind is earned by a document that states one (D114).",
    },
    {
        "question_id": "q13",
        "question": "Does any document in the corpus name the CPT codes L35755 "
                    "governs, and does it bind them to an indication list?",
        "feeds": "the us-abdominal-visceral tree's contractor_determined "
                 "binding and criterion (a)'s value set",
        "answer": "yes - A57591's ICD-10 group paragraphs name the codes in "
                  "prose, each beside the codes that support it",
        "document_id": "a57591",
        "quote": "Abdominal/visceral vascular studies of abdominal, "
                 "retroperitoneal, and pelvic organs (93975, 93976)",
        "note": "Group 1 of three; Group 2 is 93978/93979 (aorta, inferior vena "
                "cava, iliac vasculature, bypass grafts) and Group 3 is "
                "93980/93981 (penile). The article's CPT/HCPCS table is behind "
                "the AMA licence modal as A56852's and A56432's are, so this "
                "paragraph is the corpus sentence that names the codes -- and "
                "unlike L35677's revision-history line it also states what they "
                "denote and which diagnoses support them, which is D28's two "
                "classes of claim arriving together. This tree is compiled for "
                "Group 1 alone (D114).",
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
    """Fetch `url`, retrying only a 5xx and only a bounded number of times.

    A 4xx is the server saying the document is not there, which must surface
    immediately. A 5xx is the server saying it failed, which on DailyMed it
    does intermittently under no load -- and retrying it is the difference
    between this script reporting "the published document changed" and
    reporting nothing at all.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(RETRY_ON_5XX + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == RETRY_ON_5XX:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise AssertionError("unreachable: the loop either returns or raises")


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


def _collect_spl_sections(node: "ElementTree.Element", chunks: list[str]) -> None:
    """Append each outermost kept section's text, in document order.

    Recursive rather than `findall(".//section")` because the rule is about
    ancestry -- a kept section inside a kept section is already collected --
    and ElementTree elements carry no parent pointer to ask after the fact.
    """
    for child in node:
        if child.tag == "{urn:hl7-org:v3}section":
            code = child.find("{urn:hl7-org:v3}code")
            if code is not None and code.get("code") in SPL_SECTIONS:
                chunks.append("\n\n")
                chunks.append(" ".join("".join(child.itertext()).split()))
                continue
        _collect_spl_sections(child, chunks)


def extract_spl_text(data: bytes) -> str:
    """SPL XML to the plain text a knowledge-table row anchors into (T-96).

    Keeps only the sections in `SPL_SECTIONS`, in document order, joined the
    same unconditional way `extract_text` joins the MCD's -- nothing here
    depends on the content, so the same bytes always yield the same text.

    **A kept section is never descended into.** `itertext()` on a section
    already carries its numbered subsections, and whether those subsections
    carry a code of their own is a per-label formatting choice: warfarin's
    *5.1 Hemorrhage* is an unclassified section, while Zestril's is a
    `WARNINGS AND PRECAUTIONS` section in its own right. Collecting matches
    flat would therefore emit Zestril's 5.1 through 6.2 twice -- once inside
    their parent and once on their own -- which doubles a quote that should
    occur exactly once and moves every offset after it. This is the same
    guard `_SectionText.depth` applies to a nested `document-view-section`.
    """
    chunks: list[str] = []
    _collect_spl_sections(ElementTree.fromstring(data), chunks)
    text = "".join(chunks).replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text + "\n"


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


def fetch_labels(only: set[str] | None = None) -> int:
    """Download and extract the knowledge corpus (T-96, D118).

    Separate from `fetch` and not folded into it, for the reason `--only`
    exists: re-fetching a policy document re-anchors every span into it, and
    adding a drug label must not be able to do that by accident. The two
    corpora are fetched by two commands and verified by one.
    """
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    existing: dict[str, dict[str, Any]] = {}
    if only is not None:
        unknown = only - {d["document_id"] for d in DRUG_LABELS}
        if unknown:
            print(f"FAIL  --only names labels not in DRUG_LABELS: {sorted(unknown)}",
                  file=sys.stderr)
            return 1
        if not KNOWLEDGE_MANIFEST.is_file():
            print("FAIL  --only needs an existing manifest to carry the others forward",
                  file=sys.stderr)
            return 1
        manifest = json.loads(KNOWLEDGE_MANIFEST.read_text(encoding="utf-8"))
        existing = {d["document_id"]: d for d in manifest["documents"]}

    for label in DRUG_LABELS:
        doc_id = label["document_id"]
        if only is not None and doc_id not in only:
            record = existing.get(doc_id)
            path = KNOWLEDGE_DIR / label["filename"]
            if record is None or not path.is_file():
                print(f"FAIL  {doc_id} is not in the committed corpus; name it in "
                      "--only or run a full --fetch --labels", file=sys.stderr)
                return 1
            records.append(record)
            print(f"  keeping  {doc_id} as committed, sha256 {record['sha256'][:12]}")
            continue
        url = SPL_URL.format(setid=label["setid"])
        print(f"  fetching {doc_id} ... ", end="", flush=True)
        try:
            raw = download_bytes(url)
        except (urllib.error.URLError, TimeoutError) as exc:
            print("FAILED")
            print(f"FAIL  {doc_id}: {exc}", file=sys.stderr)
            return 1
        text = extract_spl_text(raw)
        records.append({
            **label,
            "url": url,
            "authority": "fda_labeling",
            "publisher": "DailyMed, U.S. National Library of Medicine",
            "format": "spl_xml",
            # D29's shape. These documents say what a drug does, never what a
            # payer covers, and nothing may cite them for a coverage claim.
            "scope": "drug_effects_only",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "extractor_version": EXTRACTOR_VERSION,
            "sections": sorted(SPL_SECTIONS),
            "sha256": sha256(text),
            "char_count": len(text),
        })
        (KNOWLEDGE_DIR / label["filename"]).write_text(text, encoding="utf-8")
        print(f"{len(text)} chars, sha256 {sha256(text)[:12]}")

    KNOWLEDGE_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_MANIFEST.write_text(
        json.dumps(
            {
                "corpus": "knowledge",
                "purpose": "FDA labeling quoted by data/knowledge/medication_effects.json. "
                           "Not a coverage corpus: nothing here may be cited for what a "
                           "payer covers (T-96, D118).",
                "extractor_version": EXTRACTOR_VERSION,
                "documents": records,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nWrote {KNOWLEDGE_MANIFEST.relative_to(REPO_ROOT)}. "
          "Now run without --fetch.")
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

    # 3b. The knowledge corpus (T-96, D118). Same three questions as the policy
    #     corpus -- present, hashing to what was recorded, re-downloadable to
    #     the same hash -- against a second manifest, because "nine policy
    #     documents" is a claim five drug labels must not be able to change.
    if not KNOWLEDGE_MANIFEST.is_file():
        bad(f"{KNOWLEDGE_MANIFEST.relative_to(REPO_ROOT)} is absent. "
            "Run with --fetch --labels first.")
    else:
        knowledge = json.loads(KNOWLEDGE_MANIFEST.read_text(encoding="utf-8"))
        label_records = {d["document_id"]: d for d in knowledge["documents"]}
        expected_labels = {d["document_id"] for d in DRUG_LABELS}
        if set(label_records) != expected_labels:
            bad(f"knowledge manifest {sorted(label_records)} does not match "
                f"DRUG_LABELS {sorted(expected_labels)}")
        if knowledge.get("extractor_version") != EXTRACTOR_VERSION:
            bad(
                f"knowledge manifest was written by extractor version "
                f"{knowledge.get('extractor_version')}, this is {EXTRACTOR_VERSION}"
            )
        for doc_id, record in sorted(label_records.items()):
            path = KNOWLEDGE_DIR / record["filename"]
            if not path.is_file():
                bad(f"{doc_id}: {record['filename']} is absent")
                continue
            text = path.read_text(encoding="utf-8")
            if sha256(text) != record["sha256"]:
                bad(f"{doc_id}: on-disk content does not match its recorded hash")
            elif len(text) != record["char_count"]:
                bad(f"{doc_id}: char_count {record['char_count']} but file holds "
                    f"{len(text)}")
            else:
                ok(f"{doc_id} present, {len(text)} chars, hash matches "
                   f"({record['ingredient']}, {record['authority']})")
            if offline:
                continue
            try:
                raw = download_bytes(record["url"])
            except (urllib.error.URLError, TimeoutError) as exc:
                bad(f"{doc_id}: re-download failed: {exc}")
                continue
            if sha256(extract_spl_text(raw)) != record["sha256"]:
                bad(
                    f"{doc_id}: re-download extracts to a different hash. The "
                    "labeler republished this SPL, and every span the knowledge "
                    "table records against it is suspect."
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
        "--labels",
        action="store_true",
        help="with --fetch: fetch the knowledge corpus (FDA labeling) instead of "
             "the policy corpus. Verification always covers both (T-96).",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip the re-download check. Does not close T-02.",
    )
    args = parser.parse_args()
    if args.only and not args.fetch:
        parser.error("--only only means something with --fetch")
    if args.labels and not args.fetch:
        parser.error("--labels only means something with --fetch; "
                     "verification always covers both corpora")
    only = {d.strip() for d in args.only.split(",") if d.strip()} if args.only else None
    if args.fetch:
        return fetch_labels(only) if args.labels else fetch(only)
    return verify(args.offline)


if __name__ == "__main__":
    raise SystemExit(main())
