from __future__ import annotations

import asyncio
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import datetime as dt
import json
import multiprocessing
import os
from pathlib import Path
import time

from . import __version__
from .config import ModelSettings
from .contracts import validate_plan_intent
from .core import CLOSE_NAMES, UnsupportedFrame, compile_frame, frame_messages, frame_prompt_language
from .engine import QueryEngine
from .ingestion import prepare_shop
from .io import dumps, fingerprint, write_json
from .output import finalize, suspicious
from .prompts import PROMPT_VERSION, context_messages
from .provider import Provider
from .repair import REWRITE_VERSION, Rewrite, acceptable, candidates, grounded, repairable, rewrite_messages
from .roles import ROLE_PROMPT_VERSION, infer_roles
from .semantics import attach_semantics
from .verify import build_verification_context, question_verification_context
from .types import Question, QueryFrame, SQLPlan
from .verify import POLICY, VERIFY_VERSION, VETO_RULE, Verdict, verify_messages, vetoed, year_mismatch


SHOP_PARALLEL = 4  # shops answered at the same time (questions stay capped by --concurrency)
PREP_WORKERS = 16  # processes ingesting shop files ahead of time (capped by CPU count and number of shops)

CONSENSUS_SEEDS = (101, 102)
CONSENSUS_TEMPERATURE = 0.5
CONSENSUS_TURNS = 3


async def consensus_loop(provider, engine, question, messages, seed, trace):
    """One independent free-form attempt: the same inspect / error-feedback turns as the single `answer` loop, but sampled
    (temperature > 0, own seed) and returning only a final, guard-checked result or None."""
    messages = list(messages)
    sample = {"seed": seed, "turns": []}
    trace["consensus"]["samples"].append(sample)
    for _ in range(CONSENSUS_TURNS):
        turn = {}
        sample["turns"].append(turn)
        try:
            plan, call = await provider.generate(messages, SQLPlan, seed=seed, temperature=CONSENSUS_TEMPERATURE)
        except Exception as error:
            turn["error"] = type(error).__name__
            return None
        call["role"] = "consensus_sql"
        trace["calls"].append(call)
        if plan is None:
            turn["error"] = call.get("error", "empty content")
            messages += [{"role": "assistant", "content": call.get("content") or "{}"},
                         {"role": "user", "content": "Kế hoạch không hợp lệ: " + turn["error"] + ". Sửa JSON đúng schema. Không kéo dài reasoning; output phải có đủ trường."}]
            continue
        turn["action"] = plan.action
        if plan.action not in ("query", "inspect"):
            return None
        turn["sql"] = plan.sql
        try:
            validate_plan_intent(plan, question)
            columns, rows = await asyncio.to_thread(engine.query, plan.sql, 30 if plan.action == "inspect" else 1000)
            if plan.action == "inspect":
                messages += [{"role": "assistant", "content": plan.model_dump_json()},
                             {"role": "user", "content": "Kết quả công cụ SQL (đã tính local):\n" + dumps({"columns": columns, "rows": rows}) + "\nHãy hoàn tất query đáp án hoặc inspect phần còn thiếu."}]
                continue
            result = finalize(plan, columns, rows)
            if flag := suspicious(result):
                turn["guard"] = flag
                return None
        except Exception as error:
            turn["error"] = str(error)[:300]
            messages += [{"role": "assistant", "content": plan.model_dump_json()},
                         {"role": "user", "content": "Lỗi SQL/validator local: " + turn["error"] + "\nSửa kế hoạch dùng đúng catalog, đúng kiểu cột và output. Nếu lỗi hệ thống không xử lý được thì abstain; không giả thành thiếu dữ liệu."}]
            continue
        sample["result"] = result
        return result
    return None


async def consensus_answer(provider, engine, question, messages, trace):
    """Guarded free-form SQL for questions no rule could compile.

    Independent attempts (different seed, temperature > 0) each run their own inspect/repair turns. The answer is
    accepted only if every attempt ends with a guard-checked result and all results are identical. Anything else
    abstains: free-form SQL alone is wrong too often, but agreeing independent attempts are rarely wrong together.
    """
    trace["consensus"] = {"seeds": list(CONSENSUS_SEEDS), "temperature": CONSENSUS_TEMPERATURE, "turns": CONSENSUS_TURNS, "samples": []}
    results = await asyncio.gather(*(consensus_loop(provider, engine, question, messages, seed, trace) for seed in CONSENSUS_SEEDS))
    agree = all(item is not None for item in results) and all(dumps(item) == dumps(results[0]) for item in results)
    trace["consensus"]["agree"] = agree
    if agree:
        return results[0], "completed_consensus"
    return {"type": "abstain"}, "consensus_disagree"


async def solve_question(question: Question, catalog: dict, rulebook: str, engine: QueryEngine, provider: Provider, *, max_calls=4, deadline=180, specialist="answer", repair="off", verify="off"):
    started = time.monotonic()
    trace = {"id": question.id, "question": question.model_dump(mode="json"), "calls": [], "executions": [], "thinking": [], "state": "planning"}
    messages = context_messages(catalog, rulebook, question.model_dump(mode="json"))
    trace["initial_messages"] = list(messages)
    answer = {"type": "abstain"}
    try:
        async with asyncio.timeout(deadline):
            capabilities = catalog.get("semantic", {}).get("capabilities", {})
            if capabilities.get("revenue") or capabilities.get("tickets"):
                frame_input = frame_messages(question, catalog)
                trace["frame_messages"] = frame_input
                frame, call = await provider.generate(frame_input, QueryFrame)
                call["role"] = "semantic_planner"
                trace["calls"].append(call)
                if call.get("thinking"):
                    trace["thinking"].append(call["thinking"])
                if frame:
                    trace["frame"] = frame.model_dump()

                    async def core_answer(current_frame, current_question):
                        plan = compile_frame(current_frame, current_question, catalog, engine)
                        trace["plan"] = plan.model_dump()
                        if plan.action == "unanswerable":
                            return {"type": "unanswerable", "reason": plan.reason}
                        validate_plan_intent(plan, current_question)
                        columns, rows = await asyncio.to_thread(engine.query, plan.sql)
                        trace["executions"].append({"sql": plan.sql, "columns": columns, "rows": rows, "compiler": "typed-frame"})
                        result = finalize(plan, columns, rows)
                        if flag := suspicious(result):
                            trace["guard"] = {"flag": flag, "suppressed": result}
                            result = {"type": "abstain"}
                        if verify == "check" and result.get("type") not in ("abstain", "unanswerable"):
                            result = await checked(current_question, plan.sql, result)
                        return result

                    async def checked(current_question, sql, result):
                        # Veto-only second opinion; it never changes a value. Original wording is what is checked.
                        record = trace["verify"] = {"version": VERIFY_VERSION, "veto_rule": VETO_RULE, "policy": list(POLICY)}
                        if year_mismatch(question.question, sql):
                            record.update(vetoed=True, rule="year_mismatch", suppressed=result)
                            return {"type": "abstain"}
                        first = await vote(sql, result, 42)
                        if first is None:
                            record["skipped"] = "deadline"
                            return result
                        record.update(first)
                        if "error" in first or not first["vetoed"]:  # a failed check keeps the compiled answer
                            return result
                        # A veto stands only when an independent second call (other seed) also vetoes. Measured on
                        # the train paraphrase splits: one call's vetoes are ~60% right, agreeing vetoes ~80%.
                        # If no second call can be made in time, the first veto stands as before.
                        second = await vote(sql, result, 43)
                        record["confirm"] = second
                        if second is not None and "error" not in second and not second["vetoed"]:
                            record["vetoed"] = False
                            return result
                        record["suppressed"] = result
                        return {"type": "abstain"}

                    async def vote(sql, result, seed):
                        remaining = deadline - (time.monotonic() - started) - 10
                        if remaining < 30:
                            return None
                        try:
                            repair_context = trace.get("repair") or {}
                            proposal = repair_context.get("rewrite") if repair_context.get("accepted") and repair_context.get("grounded") and not repair_context.get("error") else None
                            context = question_verification_context(question, catalog.get("verification_context"),
                                chat_filter_removed=":chat" in trace.get("plan",{}).get("reason",""),
                                normalization_proposal=proposal, sql=sql, rulebook_text=rulebook)
                            trace["verify"]["source_context"] = context
                            verdict, call = await asyncio.wait_for(provider.generate(verify_messages(question, sql, result, context), Verdict, thinking=True, max_tokens=8000, timeout=remaining, seed=seed), remaining)
                        except Exception as error:
                            return {"error": type(error).__name__}
                        call["role"] = "verify"
                        trace["calls"].append(call)
                        if call.get("thinking"):
                            trace["thinking"].append(call["thinking"])
                        return {"seed": seed, "verdict": verdict.model_dump() if verdict else None, "vetoed": vetoed(verdict, question.question, sql, POLICY)}

                    def done(state, result):
                        trace["state"] = state
                        trace["answer"] = {"id": question.id, **result}
                        trace["elapsed_ms"] = int((time.monotonic()-started)*1000)
                        trace["thinking"] = trace["thinking"] or None
                        trace["code"] = [entry["sql"] for entry in trace["executions"]]
                        trace["output"] = trace["executions"][-1] if trace["executions"] else None
                        return trace["answer"], trace

                    try:
                        return done("completed_core", await core_answer(frame, question))
                    except Exception as error:
                        trace["core_fallback"] = str(error)[:2000]
                        if repair == "rewrite" and repairable(trace["core_fallback"]):
                            # One rewrite with the compiler's error and names from this shop, then a normal re-plan.
                            names = await asyncio.to_thread(candidates, engine, question.question, trace["core_fallback"])
                            rewritten, call = await provider.generate(rewrite_messages(question, trace["core_fallback"], names), Rewrite)
                            call["role"] = "rewrite"
                            trace["calls"].append(call)
                            text = rewritten.question.strip() if rewritten else ""
                            trace["repair"] = {"version": REWRITE_VERSION, "names": names, "rewrite": text, "accepted": bool(rewritten) and acceptable(question.question, text, names)}
                            if trace["repair"]["accepted"]:
                                second = question.model_copy(update={"question": text})
                                frame2, call2 = await provider.generate(frame_messages(second, catalog), QueryFrame)
                                call2["role"] = "semantic_planner_repair"
                                trace["calls"].append(call2)
                                if frame2:
                                    trace["repair"]["frame"] = frame2.model_dump()
                                    trace["repair"]["grounded"] = grounded(frame.model_dump(), frame2.model_dump(), trace["core_fallback"], question.question)
                                    if trace["repair"]["grounded"]:
                                        try:
                                            result = await core_answer(frame2, second)
                                            if result.get("type") == "unanswerable":
                                                # 'Not recorded' after a rewrite may describe the rewrite, not the shop.
                                                trace["repair"]["suppressed"] = result
                                                result = {"type": "abstain"}
                                            return done("completed_repair", result)
                                        except Exception as error2:
                                            trace["repair"]["error"] = str(error2)[:1000]
                        if "entity not resolved" in trace["core_fallback"]:
                            # Last resort after the frame and its rewrite failed: one clearly closest item name (numbers agreeing).
                            token = CLOSE_NAMES.set(True)
                            try:
                                result = await core_answer(frame, question)
                                trace["close_name"] = True
                                return done("completed_close_name", result)
                            except Exception as error3:
                                trace["close_name_error"] = str(error3)[:1000]
                            finally:
                                CLOSE_NAMES.reset(token)
                        messages.append({"role": "user", "content": "Gợi ý từ bước phân tích (cần kiểm chứng): " + frame.model_dump_json() + "\nCompiler chưa hỗ trợ: " + str(error)[:1000] + "\nHãy dùng catalog và SQL để giải đúng yêu cầu gốc."})
            if specialist == "consensus":
                answer, trace["state"] = await consensus_answer(provider, engine, question, messages, trace)
            for index in range(max(0, max_calls-len(trace["calls"])) if specialist in ("answer", "unanswerable_only") else 0):
                trace["state"] = "planning" if index == 0 else "repair_or_inspect"
                plan, call = await provider.generate(messages, SQLPlan)
                trace["calls"].append(call)
                if call.get("thinking"):
                    trace["thinking"].append(call["thinking"])
                if plan is None:
                    messages += [{"role": "assistant", "content": call.get("content") or "{}"},
                                 {"role": "user", "content": "Kế hoạch không hợp lệ: " + call.get("error", "empty content") + ". Sửa JSON đúng schema. Không kéo dài reasoning; output phải có đủ trường."}]
                    continue
                trace["plan"] = plan.model_dump()
                if plan.action in ("abstain", "unanswerable"):
                    answer = {"type": plan.action}
                    if plan.action == "unanswerable":
                        answer["reason"] = plan.reason
                    trace["state"] = plan.action
                    break
                try:
                    trace["state"] = "executing"
                    validate_plan_intent(plan,question)
                    exec_started = time.monotonic()
                    columns, rows = await asyncio.to_thread(engine.query, plan.sql, 30 if plan.action == "inspect" else 1000)
                    execution = {"sql": plan.sql, "columns": columns, "rows": rows, "elapsed_ms": int((time.monotonic() - exec_started) * 1000)}
                    trace["executions"].append(execution)
                    if plan.action == "inspect":
                        messages += [{"role": "assistant", "content": plan.model_dump_json()},
                                     {"role": "user", "content": "Kết quả công cụ SQL (đã tính local):\n" + dumps({"columns": columns, "rows": rows}) + "\nHãy hoàn tất query đáp án hoặc inspect phần còn thiếu."}]
                        continue
                    trace["state"] = "verifying"
                    answer = finalize(plan, columns, rows)
                    if flag := suspicious(answer):
                        trace["guard"] = {"flag": flag, "suppressed": answer}
                        answer = {"type": "abstain"}
                    elif specialist == "unanswerable_only":
                        # Free-form SQL answers measured below 50% precision on train: abstaining has higher expected PA.
                        trace["guard"] = {"flag": "specialist_value_suppressed", "suppressed": answer}
                        answer = {"type": "abstain"}
                    trace["state"] = "completed"
                    break
                except Exception as error:
                    message = str(error)[:3000]
                    trace["executions"].append({"sql": plan.sql, "error": message})
                    messages += [{"role": "assistant", "content": plan.model_dump_json()},
                                 {"role": "user", "content": "Lỗi SQL/validator local: " + message + "\nSửa kế hoạch dùng đúng catalog, đúng kiểu cột và output. Nếu lỗi hệ thống không xử lý được thì abstain; không giả thành thiếu dữ liệu."}]
            else:
                if specialist != "consensus":
                    trace["state"] = "specialist_off" if specialist == "off" else "call_budget_exhausted"
    except TimeoutError:
        trace["state"] = "deadline_exceeded"
    except Exception as error:
        trace["state"] = "failed"
        trace["error"] = str(error)[:2000].replace(provider.settings.api_key, "<redacted>")
    answer = {"id": question.id, **answer}
    trace["answer"] = answer
    trace["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    trace["thinking"] = trace["thinking"] or None
    trace["code"] = [entry["sql"] for entry in trace["executions"]]
    trace["output"] = trace["executions"][-1] if trace["executions"] else None
    return answer, trace


async def run_questions(split: Path, questions: list[Question], out: Path, settings: ModelSettings, cache: Path, *, concurrency=2, max_calls=4, deadline=180, resume=False, specialist="answer", role_inference="fallback", repair="off", verify="off"):
    split = split.resolve()
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    submission = out / "submission.jsonl"
    question_ids = [question.id for question in questions]
    if len(set(question_ids)) != len(question_ids):
        raise ValueError("Duplicate question IDs")
    if submission.exists() and not resume:
        raise ValueError("Run output exists; use a new --out or explicit --resume")
    manifest_path = out / "manifest.json"
    config = {"package_version": __version__, "prompt_version": PROMPT_VERSION, "split": str(split),
              "question_ids": question_ids, "model": settings.public_dict(), "max_calls": max_calls, "deadline_seconds": deadline, "concurrency": concurrency, "specialist": specialist, "role_inference": role_inference, "role_prompt_version": ROLE_PROMPT_VERSION, "frame_prompt": frame_prompt_language(), "repair": repair, "rewrite_version": REWRITE_VERSION, "verify": verify, "verify_version": VERIFY_VERSION, "veto_rule": VETO_RULE,
              "rulebook_hash": fingerprint([split / "rulebook.md"]), "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    config["solver_hash"] = fingerprint(list(Path(__file__).parent.glob("*.py")))
    config["input_hash"] = fingerprint([split / "rulebook.md", split / "calendar.csv"] + [p for business in sorted({q.business_id for q in questions}) for p in (split / "data" / business).iterdir() if p.suffix.lower() in {".md", ".csv", ".xlsx", ".sqlite"}])
    config["questions_hash"] = __import__('hashlib').sha256(''.join(q.model_dump_json() for q in questions).encode()).hexdigest()
    completed = set()
    if resume and manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        for key in ("question_ids", "prompt_version", "model", "rulebook_hash", "split", "solver_hash", "input_hash", "questions_hash", "max_calls", "deadline_seconds", "specialist", "role_inference", "role_prompt_version", "repair", "rewrite_version", "verify", "verify_version", "veto_rule"):
            if previous.get(key) != config.get(key):
                raise ValueError(f"Resume config changed: {key}; use a new run directory")
        if submission.exists():
            records = []
            for line in submission.read_text().splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get("id") in completed or item.get("id") not in set(question_ids):
                    raise ValueError("Invalid/duplicate IDs in resume output")
                completed.add(item["id"])
                records.append(item)
            submission.write_text("".join(dumps(item) + "\n" for item in records))
    write_json(manifest_path, config)
    (out / "questions.jsonl").write_text("".join(question.model_dump_json() + "\n" for question in questions))
    logs = out / "logs"
    logs.mkdir(exist_ok=True)
    rulebook = (split / "rulebook.md").read_text()
    by_business = defaultdict(list)
    for question in questions:
        if question.id not in completed:
            by_business[question.business_id].append(question)
    provider = Provider(settings)
    semaphore = asyncio.Semaphore(concurrency)  # global cap on questions in flight, across all shops
    started = time.monotonic()
    config["shops"] = {}
    # Pipelining (speed only; per-question logic is unchanged): file ingestion of every shop starts at once in a
    # process pool (CPU-bound, one core each, flock-safe per shop), and up to SHOP_PARALLEL shops are answered
    # concurrently, so one shop's preparation / role call / slow questions overlap the others' GPU work.
    loop = asyncio.get_running_loop()
    pool = ProcessPoolExecutor(max_workers=max(1, min(PREP_WORKERS, os.cpu_count() or 1, len(by_business))),
                               mp_context=multiprocessing.get_context("spawn"))
    prepared = {business: loop.run_in_executor(pool, prepare_shop, split, business, cache) for business in sorted(by_business)}
    shop_gate = asyncio.Semaphore(SHOP_PARALLEL)
    try:
        with submission.open("a", encoding="utf-8") as writer:
            async def run_shop(business, batch):
              async with shop_gate:
                prep_started = time.monotonic()
                print(dumps({"event": "prepare", "business": business, "questions": len(batch)}), flush=True)
                directory = await prepared[business]
                catalog = json.loads((directory / "catalog.json").read_text())
                config["shops"][business] = {"fingerprint": catalog["fingerprint"], "prepare_ms": int((time.monotonic() - prep_started) * 1000)}
                if role_inference != "off":
                    # One cached call per shop; used only where alias matching finds nothing.
                    try:
                        roles, call = await infer_roles(provider, catalog, directory)
                    except Exception as error:
                        roles, call = {"domain": "other", "tables": {}}, {"error": str(error)[:300].replace(settings.api_key, "<redacted>")}
                    catalog["llm_roles"] = roles
                    config["shops"][business]["roles"] = {"domain": roles.get("domain"), "cached": call is None, "usage": (call or {}).get("usage"), "error": (call or {}).get("error")}
                write_json(out / "catalogs" / f"{business}.json", catalog)
                engine = QueryEngine(directory / "store.duckdb", {table["name"] for table in catalog["tables"]})
                try:
                    semantic_started = time.monotonic()
                    try:
                        # Off the event loop: this takes up to ~80 s per shop and must not stall other shops' questions.
                        await asyncio.to_thread(attach_semantics, engine, catalog)
                        if verify == "check":
                            catalog["verification_context"] = await asyncio.to_thread(build_verification_context, catalog, engine, rulebook_text=rulebook)
                    except Exception as error:
                        catalog["semantic"] = {"warnings": [str(error)[:2000]], "capabilities": {}}
                    config["shops"][business]["semantic_prepare_ms"] = int((time.monotonic()-semantic_started)*1000)
                    write_json(out / "catalogs" / f"{business}.json", catalog)
                    async def worker(question):
                        async with semaphore:
                            answer, trace = await solve_question(question, catalog, rulebook, engine, provider, max_calls=max_calls, deadline=deadline, specialist=specialist, repair=repair, verify=verify)
                            write_json(logs / f"{question.id}.json", trace)
                            writer.write(dumps(answer) + "\n")
                            writer.flush()
                            completed.add(question.id)
                            print(dumps({"event": "answer", "id": question.id, "business": business, "type": answer["type"], "state": trace["state"],
                                         "elapsed_ms": trace["elapsed_ms"], "calls": len(trace["calls"]), "done": len(completed), "total": len(questions)}), flush=True)
                    await asyncio.gather(*(worker(question) for question in batch))
                finally:
                    engine.close()
            # A failing shop no longer stops the others; the first error is still raised once all shops are done.
            outcomes = await asyncio.gather(*(run_shop(business, batch) for business, batch in sorted(by_business.items())), return_exceptions=True)
            errors = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
            if errors:
                raise errors[0]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        await provider.close()
        config.update(elapsed_ms=int((time.monotonic() - started) * 1000), completed=len(completed), total=len(questions), complete=len(completed) == len(questions))
        write_json(manifest_path, config)
    # No gold dependency: these are execution/usage metrics only.
    traces = [json.loads(path.read_text()) for path in logs.glob("*.json")]
    usage = defaultdict(int)
    role_calls = [shop["roles"] for shop in config["shops"].values() if shop.get("roles") and not shop["roles"].get("cached")]
    for call in role_calls:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage[key] += (call.get("usage") or {}).get(key, 0)
    for trace in traces:
        for call in trace["calls"]:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usage[key] += (call.get("usage") or {}).get(key, 0)
    write_json(out / "execution_metrics.json", {"n": len(traces), "model_calls": sum(len(trace["calls"]) for trace in traces) + len(role_calls), "role_calls": len(role_calls), "usage": dict(usage),
                                               "elapsed_ms": config["elapsed_ms"], "complete": config["complete"]})
    return config
