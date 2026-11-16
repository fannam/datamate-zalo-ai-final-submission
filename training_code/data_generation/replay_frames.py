"""Offline replay: recompile the frames stored in earlier run logs with the CURRENT code. No model calls.

Isolates rule changes from model noise. Questions whose stored frame no longer compiles (they needed the
model's rewrite/repair path) keep their old answer and are reported separately.

usage: python training_code/data_generation/replay_frames.py --runs runs/a runs/b --gold eval_sets/variants_120_gold.jsonl [--split public_test]
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from datamate.contracts import validate_plan_intent
from datamate.core import compile_frame
from datamate.engine import QueryEngine
from datamate.evaluation import score_answer
from datamate.ingestion import prepare_shop
from datamate.output import finalize, suspicious
from datamate.semantics import attach_semantics
from datamate.types import QueryFrame, Question


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def replay_one(frame, question, catalog, engine):
    plan = compile_frame(frame, question, catalog, engine)
    if plan.action == "unanswerable":
        return {"type": "unanswerable", "reason": plan.reason}
    try:
        validate_plan_intent(plan, question)
    except ValueError:  # live, a validator rejection ends as an abstain unless the repair path recovers it
        return {"type": "abstain", "validator": True}
    columns, rows = engine.query(plan.sql)
    result = finalize(plan, columns, rows)
    return {"type": "abstain"} if suspicious(result) else result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--split", type=Path, default=Path("public_test"))
    parser.add_argument("--cache", type=Path, default=Path(".cache"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    gold = {row["id"]: row for row in load_jsonl(args.gold)}
    items = []  # (question, trace, old_answer, run)
    for run in args.runs:
        old = {row["id"]: row for row in load_jsonl(run / "submission.jsonl")}
        for question in load_jsonl(run / "questions.jsonl"):
            trace = json.loads((run / "logs" / f"{question['id']}.json").read_text(encoding="utf-8"))
            items.append((Question(**question), trace, old[question["id"]], run))
    rows, engines = [], {}
    try:
        for question, trace, old_answer, run in sorted(items, key=lambda item: item[0].id):
            business = question.business_id
            if business not in engines:
                directory = prepare_shop(args.split, business, args.cache)
                catalog = json.loads((run / "catalogs" / f"{business}.json").read_text(encoding="utf-8"))
                engine = QueryEngine(directory / "store.duckdb", {table["name"] for table in catalog["tables"]})
                attach_semantics(engine, catalog)
                engines[business] = (catalog, engine)
            catalog, engine = engines[business]
            status = "replayed"
            if not trace.get("frame"):
                new_answer, status = {k: v for k, v in old_answer.items() if k != "id"}, "no_frame"
            else:
                try:
                    new_answer = replay_one(QueryFrame(**trace["frame"]), question, catalog, engine)
                except Exception as error:  # needed the model's repair path or is unsupported
                    new_answer, status = {k: v for k, v in old_answer.items() if k != "id"}, "kept_old:" + type(error).__name__
            reference = gold[question.id]
            old_score, _ = score_answer(old_answer, reference)
            new_score, _ = score_answer({"id": question.id, **new_answer}, reference)
            rows.append({"id": question.id, "status": status, "old": old_score, "new": new_score,
                         "old_answer": old_answer, "new_answer": new_answer, "question": question.question})
    finally:
        for _, engine in engines.values():
            engine.close()
    if args.out:  # write first: a reporting error below must not lose the replay
        args.out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    def summary(key, subset):
        counts = Counter(row[key] for row in subset)
        n = len(subset)
        if not n:
            return "n=0"
        return f"n={n} correct={counts[1]} wrong={counts[-1]} abstain={counts[0]} PA={(counts[1]-counts[-1])/n:.3f}"
    for label, subset in (("all", rows), ("originals", [r for r in rows if r["id"].endswith("_p0")]),
                          ("variants", [r for r in rows if not r["id"].endswith("_p0")])):
        print(f"{label:9s} old: {summary('old', subset)}\n{'':9s} new: {summary('new', subset)}")
    print("status:", dict(Counter(row["status"].split(":")[0] for row in rows)))
    for row in rows:
        if row["old"] != row["new"]:
            print(f"CHANGED {row['id']} {row['old']:+d}->{row['new']:+d} {row['question'][:70]} => {json.dumps(row['new_answer'], ensure_ascii=False)[:60]}")


if __name__ == "__main__":
    main()
