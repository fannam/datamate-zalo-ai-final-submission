from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import json
from pathlib import Path
import random
import shutil

from .config import ModelSettings
from .ingestion import prepare_shop
from .io import dumps, load_jsonl, write_json
from .types import Question


def main():
    parser = argparse.ArgumentParser(prog="datamate")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="Read/normalize business files locally, no model calls")
    prepare.add_argument("--split", type=Path, required=True)
    prepare.add_argument("--business", nargs="+")
    prepare.add_argument("--cache", type=Path, default=Path(".cache"))
    prepare.add_argument("--force", action="store_true")
    sample = sub.add_parser("sample", help="Create an inference-only question manifest (no answer fields)")
    sample.add_argument("--split", type=Path, default=Path("train"))
    sample.add_argument("--business", nargs="+")
    sample.add_argument("--per-business", type=int, default=4)
    sample.add_argument("--seed", type=int, default=42)
    sample.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run", help="Run the configured model and SQL tools; never reads gold")
    run.add_argument("--split", type=Path, required=True)
    run.add_argument("--questions", type=Path)
    run.add_argument("--business", nargs="+")
    run.add_argument("--limit", type=int)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--cache", type=Path, default=Path(".cache"))
    run.add_argument("--env-file", type=Path, default=Path(".env"))
    run.add_argument("--concurrency", type=int, default=2)
    run.add_argument("--max-calls", type=int, default=4)
    run.add_argument("--max-tokens", type=int, default=3000)
    run.add_argument("--thinking-temperature", type=float, help="Sampling temperature for thinking calls only (Qwen recommends 0.6; greedy thinking loops)")
    run.add_argument("--stream", action="store_true", help="Stream completions (needed for long thinking calls behind gateways that cut requests after a few minutes)")
    run.add_argument("--timeout", type=float, default=90, help="HTTP read timeout per model call in seconds (thinking calls need several minutes)")
    run.add_argument("--deadline", type=float, default=180)
    run.add_argument("--thinking", action="store_true")
    run.add_argument("--temperature", type=float, default=0.6)
    run.add_argument("--specialist", choices=["answer", "unanswerable_only", "off", "consensus"], default="answer", help="What to do with free-form SQL specialist results; consensus answers only when two independent samples agree")
    run.add_argument("--role-inference", choices=["fallback", "off"], default="fallback", help="One model call per shop mapping unseen column headers to roles; aliases stay first")
    run.add_argument("--verify", choices=["check", "off"], default="off", help="Veto-only second opinion (thinking) on each compiled answer: SQL vs question; a veto becomes abstain")
    run.add_argument("--repair", choices=["rewrite", "off"], default="off", help="After a compiler rejection (unresolved name/filter/time), one model rewrite of the question with names from the data, then a normal re-plan")
    run.add_argument("--resume", action="store_true")
    evaluate = sub.add_parser("evaluate", help="Separate evaluator: reads train gold AFTER inference")
    evaluate.add_argument("--run", type=Path, required=True)
    evaluate.add_argument("--answers", type=Path, default=Path("train/answers.jsonl"))
    validate = sub.add_parser("validate", help="Check submission schema and IDs without gold")
    validate.add_argument("--questions", type=Path, required=True)
    validate.add_argument("--submission", type=Path, required=True)
    validate.add_argument("--require-complete", action="store_true")
    export = sub.add_parser("export", help="Validate a complete run then copy its JSONL for portal upload")
    export.add_argument("--run", type=Path, required=True)
    export.add_argument("--output", type=Path, default=Path("submission.jsonl"))
    splits = sub.add_parser("make-splits", help="Fixed shop-grouped build/dev/lockbox manifests")
    splits.add_argument("--split", type=Path, default=Path("train"))
    splits.add_argument("--output", type=Path, default=Path("manifests"))
    splits.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.command == "prepare":
        businesses = args.business or sorted(path.name for path in (args.split / "data").iterdir() if path.is_dir())
        for business in businesses:
            directory = prepare_shop(args.split, business, args.cache, force=args.force)
            print(dumps({"business": business, "cache": str(directory)}), flush=True)
    elif args.command == "sample":
        questions = defaultdict(list)
        for question in load_jsonl(args.split / "questions.jsonl"):
            if not args.business or question["business_id"] in args.business:
                questions[question["business_id"]].append(question)
        rng = random.Random(args.seed)
        selected = []
        for _, rows in sorted(questions.items()):
            selected += rng.sample(rows, min(args.per_business, len(rows)))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("".join(dumps(row) + "\n" for row in selected))
        print(dumps({"questions": len(selected), "businesses": len(questions), "output": str(args.output)}))
    elif args.command == "run":
        from .runtime import run_questions
        questions = [Question.model_validate(question) for question in load_jsonl(args.questions or args.split / "questions.jsonl")]
        if args.business:
            questions = [q for q in questions if q.business_id in args.business]
        if args.limit:
            questions = questions[:args.limit]
        settings = ModelSettings.from_env(args.env_file, max_tokens=args.max_tokens, thinking=args.thinking, temperature=args.temperature, timeout=args.timeout, stream=args.stream, thinking_temperature=args.thinking_temperature)
        if args.concurrency < 1 or args.max_calls < 1:
            parser.error("concurrency and max-calls must be positive")
        result = asyncio.run(run_questions(args.split, questions, args.out, settings, args.cache, concurrency=args.concurrency,
                                          max_calls=args.max_calls, deadline=args.deadline, resume=args.resume, specialist=args.specialist,
                                          role_inference=args.role_inference, repair=args.repair, verify=args.verify))
        print(dumps({key: result[key] for key in ('completed', 'total', 'complete', 'elapsed_ms')}))
    elif args.command == "evaluate":
        from .evaluation import evaluate
        report = evaluate(args.run / "questions.jsonl", args.answers, args.run / "submission.jsonl")
        write_json(args.run / "evaluation.json", report)
        markdown = ["# Local evaluation", "", "Scorer: specification-based; official parity not verified.", "", dumps(report["overall"], indent=2), "", "## Errors", ""]
        for row in report["questions"]:
            if row["score"] != 1:
                markdown += [f"### {row['id']} ({row['business_id']})", row["question"], "", "Prediction: " + dumps(row["prediction"]), "", "Reference: " + dumps(row["reference"]), ""]
        (args.run / "evaluation.md").write_text("\n".join(markdown))
        print(dumps({"overall": report["overall"], "by_type": report["by_type"]}, indent=2))
    elif args.command == "validate":
        print(dumps(validate_submission(args.questions, args.submission, args.require_complete), indent=2))
    elif args.command == "export":
        result = validate_submission(args.run / "questions.jsonl", args.run / "submission.jsonl", True)
        if args.output.exists():
            raise ValueError("Export destination already exists; choose a new output path")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.run / "submission.jsonl", args.output)
        print(dumps({**result, "output": str(args.output)}))
    elif args.command == "make-splits":
        rows = list(load_jsonl(args.split / "questions.jsonl"))
        shops = sorted({row["business_id"] for row in rows})
        random.Random(args.seed).shuffle(shops)
        boundaries = (int(len(shops) * .6), int(len(shops) * .8))
        groups = {"build": shops[:boundaries[0]], "dev": shops[boundaries[0]:boundaries[1]], "lockbox": shops[boundaries[1]:]}
        args.output.mkdir(parents=True, exist_ok=True)
        write_json(args.output / "groups.json", {"seed": args.seed, "groups": groups, "note": "Shop-grouped random split; inspect domain/layout balance before relying on estimates."})
        for group, members in groups.items():
            (args.output / f"{group}.jsonl").write_text("".join(dumps(row) + "\n" for row in rows if row["business_id"] in members))
        print(dumps({key: len(value) for key, value in groups.items()}))


def validate_submission(questions_path: Path, submission_path: Path, require_complete=False):
    import math
    from .evaluation import score_answer
    questions = {q["id"] for q in load_jsonl(questions_path)}
    seen, counts = set(), defaultdict(int)
    for row in load_jsonl(submission_path):
        qid = row.get("id")
        if qid not in questions or qid in seen:
            raise ValueError(f"Unknown/duplicate submission ID: {qid}")
        seen.add(qid)
        kind = row.get("type")
        counts[kind] += 1
        if kind not in {"number", "percent", "string", "date", "boolean", "list", "table", "unanswerable", "abstain"}:
            raise ValueError(f"Unknown answer type: {kind}")
        if "reason" in row and not isinstance(row["reason"], str):
            raise ValueError("reason must be a string")
        if kind in {"unanswerable", "abstain"}:
            continue
        answer = row.get("answer")
        # A self-reference checks type/shape without any gold values.
        if kind in {"number", "percent"} and (type(answer) not in {int, float} or not math.isfinite(answer)):
            raise ValueError(f"Invalid numeric answer: {qid}")
        if score_answer(row, {"type": kind, "answer": answer, "alias": []})[0] != 1:
            raise ValueError(f"Invalid answer format: {qid}")
    if require_complete and seen != questions:
        raise ValueError(f"Incomplete submission: missing {len(questions - seen)} questions")
    return {"questions": len(questions), "answers": len(seen), "missing": len(questions - seen), "types": dict(counts)}
