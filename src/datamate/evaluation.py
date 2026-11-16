"""Specification-based scorer. Deliberately separate from inference modules."""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import math
from pathlib import Path
import re
import unicodedata

from .io import load_jsonl


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFC", value).lower()
    return " ".join("".join(c if c.isalnum() or c.isspace() else " " for c in text).split())


def numeric_equal(predicted, expected, *, percent=False):
    if isinstance(predicted, bool) or not isinstance(predicted, (int, float, Decimal)):
        return False
    try:
        left, right = Decimal(str(predicted)), Decimal(str(expected))
        if not left.is_finite() or not right.is_finite():
            return False
        if percent:
            from .parsing import round_decimal
            return round_decimal(left, 2) == round_decimal(right, 2)
        if left == right:
            return True
        # Only representation-scale floating error, never a percentage tolerance.
        tolerance = max(1e-9, 2 * math.ulp(float(expected)))
        return abs(left - right) <= Decimal(str(tolerance))
    except (InvalidOperation, OverflowError, ValueError):
        return False


def cell_equal(predicted, expected, aliases=()):
    if isinstance(expected, bool):
        return type(predicted) is bool and predicted == expected
    if isinstance(expected, (int, float, Decimal)):
        return numeric_equal(predicted, expected)
    if isinstance(expected, str):
        if not isinstance(predicted, str):
            return False
        alternatives = [expected] + [x for x in aliases if isinstance(x, str)]
        return normalize_name(predicted) in {normalize_name(x) for x in alternatives}
    return predicted == expected


def table_equal(predicted, expected, aliases):
    if not isinstance(predicted, dict) or set(predicted) != {"columns", "rows"}:
        return False
    cols, rows = predicted["columns"], predicted["rows"]
    if not isinstance(cols, list) or not all(isinstance(c, str) for c in cols) or not isinstance(rows, list):
        return False
    expected_cols, expected_rows = expected["columns"], expected["rows"]
    if len(cols) != len(expected_cols) or len(rows) != len(expected_rows):
        return False
    normalized = [normalize_name(c) for c in cols]
    if len(set(normalized)) != len(normalized):
        return False
    indices = [normalized.index(normalize_name(c)) if normalize_name(c) in normalized else i for i, c in enumerate(expected_cols)]
    if len(set(indices)) != len(indices):
        return False
    if any(not isinstance(row, list) or len(row) != len(cols) for row in rows):
        return False
    edges = []
    for i, reference in enumerate(expected_rows):
        row_aliases = aliases[i] if i < len(aliases) and isinstance(aliases[i], list) else []
        edges.append([j for j, row in enumerate(rows) if all(
            cell_equal(row[indices[k]], value, row_aliases if k == 0 else ()) for k, value in enumerate(reference)
        )])
    matched = {}

    def augment(i, seen):
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in matched or augment(matched[j], seen):
                matched[j] = i
                return True
        return False

    return all(augment(i, set()) for i in range(len(edges)))


def score_answer(prediction: dict | None, reference: dict) -> tuple[int, str]:
    if prediction is None or prediction.get("type") == "abstain":
        return 0, "abstain"
    if "reason" in prediction and not isinstance(prediction["reason"], str):
        return 0, "unreadable_reason"
    kind = prediction.get("type")
    if kind != reference["type"]:
        return -1, "type_mismatch"
    if kind == "unanswerable":
        return 1, "correct"
    if "answer" not in prediction:
        return -1, "missing_answer"
    answer, expected = prediction["answer"], reference["answer"]
    aliases = reference.get("alias") or []
    if kind in ("number", "percent"):
        correct = numeric_equal(answer, expected, percent=kind == "percent")
    elif kind == "boolean":
        correct = type(answer) is bool and answer == expected
    elif kind == "date":
        correct = isinstance(answer, str) and bool(re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", answer)) and answer == expected
    elif kind == "string":
        correct = cell_equal(answer, expected, aliases)
    elif kind == "list":
        correct = isinstance(answer, list) and len(answer) == len(expected) and all(
            cell_equal(value, target, aliases[i] if i < len(aliases) and isinstance(aliases[i], list) else ())
            for i, (value, target) in enumerate(zip(answer, expected))
        )
    elif kind == "table":
        correct = table_equal(answer, expected, aliases)
    else:
        correct = False
    return (1, "correct") if correct else (-1, "value_or_format")


def summarize(scores):
    counts = Counter(score for score in scores)
    total = len(scores)
    answered = counts[1] + counts[-1]
    return {
        "n": total, "correct": counts[1], "wrong": counts[-1], "abstain": counts[0],
        "pa": (counts[1] - counts[-1]) / total if total else 0,
        "coverage": answered / total if total else 0,
        "answered_precision": counts[1] / answered if answered else None,
    }


def evaluate(questions_path: Path, answers_path: Path, submission_path: Path):
    questions = list(load_jsonl(questions_path))
    question_map = {q["id"]: q for q in questions}
    references = {a["id"]: a for a in load_jsonl(answers_path)}
    predictions = {}
    for prediction in load_jsonl(submission_path):
        qid = prediction.get("id")
        if qid in predictions:
            raise ValueError(f"Duplicate prediction ID: {qid}")
        if qid not in question_map:
            raise ValueError(f"Unknown prediction ID for evaluation scope: {qid}")
        predictions[qid] = prediction
    missing_gold = set(question_map) - references.keys()
    if missing_gold:
        raise ValueError(f"Missing gold for {len(missing_gold)} questions")
    rows, business_scores, type_scores = [], defaultdict(list), defaultdict(list)
    for question in questions:
        qid = question["id"]
        score, reason = score_answer(predictions.get(qid), references[qid])
        rows.append({"id": qid, "business_id": question["business_id"], "question": question["question"],
                     "score": score, "comparison": reason, "prediction": predictions.get(qid), "reference": references[qid]})
        business_scores[question["business_id"]].append(score)
        type_scores[references[qid]["type"]].append(score)
    return {
        "scorer": "local-spec-v1; official scorer parity not yet verified",
        "overall": summarize([row["score"] for row in rows]),
        "by_business": {key: summarize(value) for key, value in sorted(business_scores.items())},
        "by_type": {key: summarize(value) for key, value in sorted(type_scores.items())},
        "questions": rows,
    }
