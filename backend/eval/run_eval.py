"""Evaluation harness for retrieval and answer quality.

Without this, every change to chunking, retrieval or the prompt is a guess. The
pipeline has already produced a confidently wrong answer -- cataract described
as "excluded" when the policy lists it as a specified condition with a waiting
period -- and nothing in the test suite would catch that, because it is not a
crash, it is a plausible sentence.

What is measured, and why each one:

  recall@k     did retrieval surface a document that can answer the question?
               If this fails nothing downstream can succeed.
  MRR          how far down the list. Rank 1 and rank 6 both "hit" but the model
               reads rank 1 more carefully.
  answer terms does the answer contain the figure it must contain? Cheap, exact,
               and catches a whole class of silent regression.
  faithfulness is every claim supported by a retrieved excerpt? This is the
               hallucination measure, judged by a model against the excerpts
               that were actually retrieved -- not against its own knowledge.
  refusal      on questions the corpus cannot answer, did it decline? A
               confident invented answer is the worst failure available here.

Usage:
    python eval/run_eval.py                 # run, print a report
    python eval/run_eval.py --save          # also write eval/results/<sha>.json
    python eval/run_eval.py --check         # compare to baseline, exit 1 on regression
    python eval/run_eval.py --retrieval-only  # no LLM calls, no spend
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.compat import asyncio_run  # noqa: E402
from app.config import settings  # noqa: E402
from app.rag.embed import get_embedding_provider  # noqa: E402
from app.rag.retrieve import RetrievedChunk, retrieve  # noqa: E402
from app.rag.synthesize import NO_CONTEXT_ANSWER, has_usable_context, stream_answer  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
GOLDEN_SET = EVAL_DIR / "golden_set.yaml"
RESULTS_DIR = EVAL_DIR / "results"
BASELINE = EVAL_DIR / "baseline.json"

# How far a regression may drift before CI fails. Small movements are noise from
# model non-determinism; a real regression moves further than this.
TOLERANCE = {
    "recall_at_5": 0.05,
    "mrr": 0.05,
    "faithfulness": 0.10,
    "refusal_accuracy": 0.01,  # refusing correctly is close to binary; hold it tight
    "term_accuracy": 0.10,
}

JUDGE_PROMPT = """You are grading whether an answer is supported by source excerpts.

You are NOT judging whether the answer is true in general, or whether it is a
good answer. Only this: is every factual claim in the answer traceable to the
excerpts below?

Reply with JSON only:
{"supported": <count of claims supported by the excerpts>,
 "unsupported": <count of claims not in the excerpts>,
 "verdict": "faithful" | "partially_faithful" | "unfaithful",
 "reason": "<one sentence>"}

Treat as unsupported: figures not in the excerpts, coverage stated more strongly
than the excerpts allow (for example calling a waiting-period condition
"excluded"), and any claim drawn from general knowledge of insurance."""


@dataclass
class CaseResult:
    id: str
    question: str
    should_refuse: bool
    # retrieval
    hit_at_5: bool = False
    hit_at_10: bool = False
    reciprocal_rank: float = 0.0
    retrieved: list[str] = field(default_factory=list)
    dense_and_sparse: int = 0
    scoring: str = "none"  # content | document | none
    # answer
    answer: str = ""
    refused: bool = False
    refusal_correct: bool | None = None
    terms_found: list[str] = field(default_factory=list)
    terms_missing: list[str] = field(default_factory=list)
    term_ok: bool | None = None
    faithful: str | None = None
    faithfulness_reason: str = ""
    latency_ms: int = 0


def _git_sha() -> str:
    try:
        # S607: resolving git from PATH is intended -- this only stamps results
        # with a commit sha and never runs on a request path.
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def load_cases() -> list[dict[str, Any]]:
    return yaml.safe_load(GOLDEN_SET.read_text(encoding="utf-8"))


def score_retrieval(case: dict[str, Any], hits: list[RetrievedChunk], result: CaseResult) -> None:
    """Score on retrieved content where the case provides an anchor.

    Filename matching was the original approach and it was close to worthless:
    with six documents and two or three listed as acceptable, almost any result
    "hits". It reported recall@5 of 100% while the answering chunk was absent
    from the top six and the model was answering the wrong question.

    `expect_content` fixes that -- a distinctive phrase from the clause that
    actually answers the question. A hit then means "the answering text was
    retrieved", which is what the metric was always supposed to mean. Cases
    without an anchor fall back to filename scoring, and are counted separately
    so a weak signal is never mistaken for a strong one.
    """
    result.retrieved = [h.filename for h in hits]
    result.dense_and_sparse = sum(1 for h in hits if h.dense_rank and h.sparse_rank)

    anchors = [a.lower() for a in case.get("expect_content", [])]
    if anchors:
        result.scoring = "content"
        for rank, hit in enumerate(hits, start=1):
            haystack = f"{hit.section_path} {hit.body}".lower()
            if any(a in haystack for a in anchors):
                result.reciprocal_rank = 1.0 / rank
                result.hit_at_5 = rank <= 5
                result.hit_at_10 = rank <= 10
                return
        return

    expected = {s.lower() for s in case.get("expect_sources", [])}
    if not expected:
        return
    result.scoring = "document"
    for rank, hit in enumerate(hits, start=1):
        if hit.filename.lower() in expected:
            result.reciprocal_rank = 1.0 / rank
            result.hit_at_5 = rank <= 5
            result.hit_at_10 = rank <= 10
            return


async def judge_faithfulness(answer: str, hits: list[RetrievedChunk]) -> tuple[str, str]:
    from openai import AsyncOpenAI

    excerpts = "\n\n".join(f"[{i}] {h.body[:900]}" for i, h in enumerate(hits, 1))
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    resp = await client.chat.completions.create(
        model=settings.CHAT_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": f"EXCERPTS:\n{excerpts}\n\nANSWER:\n{answer}"},
        ],
    )
    try:
        data = json.loads(resp.choices[0].message.content or "{}")
        return str(data.get("verdict", "unknown")), str(data.get("reason", ""))
    except json.JSONDecodeError:
        return "unknown", "judge returned malformed JSON"


async def run(retrieval_only: bool) -> list[CaseResult]:
    engine = create_async_engine(str(settings.DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = get_embedding_provider()
    results: list[CaseResult] = []

    for case in load_cases():
        res = CaseResult(
            id=case["id"], question=case["question"], should_refuse=case.get("should_refuse", False)
        )
        started = time.perf_counter()

        async with factory() as db:
            hits = await retrieve(db, case["question"], provider, user_id=None, limit=10)
        score_retrieval(case, hits, res)

        if not retrieval_only:
            top = hits[: settings.RETRIEVAL_TOP_K]
            res.answer = "".join([t async for t in stream_answer(case["question"], top)])
            res.refused = res.answer.strip().startswith(NO_CONTEXT_ANSWER[:30])
            res.refusal_correct = res.refused == res.should_refuse

            if not res.should_refuse:
                terms = case.get("expect_terms") or []
                low = res.answer.lower()
                res.terms_found = [t for t in terms if t.lower() in low]
                res.terms_missing = [t for t in terms if t.lower() not in low]
                res.term_ok = not res.terms_missing if terms else None

                if has_usable_context(top) and not res.refused:
                    res.faithful, res.faithfulness_reason = await judge_faithfulness(
                        res.answer, top
                    )

        res.latency_ms = int((time.perf_counter() - started) * 1000)
        results.append(res)
        print(
            f"  {res.id:28} {'refuse' if res.should_refuse else 'answer':6} "
            f"rr={res.reciprocal_rank:.2f} {res.faithful or ''}"
        )

    await engine.dispose()
    return results


def summarise(results: list[CaseResult]) -> dict[str, Any]:
    answerable = [r for r in results if not r.should_refuse]
    refusals = [r for r in results if r.should_refuse]
    scored = [r for r in answerable if r.scoring != "none"]
    strict = [r for r in scored if r.scoring == "content"]
    judged = [r for r in answerable if r.faithful]
    termed = [r for r in answerable if r.term_ok is not None]
    graded = [r for r in results if r.refusal_correct is not None]

    def frac(n: int, d: int) -> float:
        return round(n / d, 4) if d else 0.0

    return {
        "cases": len(results),
        "recall_at_5": frac(sum(r.hit_at_5 for r in scored), len(scored)),
        "recall_at_5_content": frac(sum(r.hit_at_5 for r in strict), len(strict)),
        "content_scored_cases": len(strict),
        "recall_at_10": frac(sum(r.hit_at_10 for r in scored), len(scored)),
        "mrr": round(statistics.mean([r.reciprocal_rank for r in scored]), 4) if scored else 0.0,
        "faithfulness": frac(sum(r.faithful == "faithful" for r in judged), len(judged)),
        "unfaithful_count": sum(r.faithful == "unfaithful" for r in judged),
        "term_accuracy": frac(sum(bool(r.term_ok) for r in termed), len(termed)),
        "refusal_accuracy": frac(sum(bool(r.refusal_correct) for r in graded), len(graded)),
        "refusal_cases": len(refusals),
        "median_latency_ms": int(statistics.median([r.latency_ms for r in results]))
        if results
        else 0,
    }


def report(summary: dict[str, Any], results: list[CaseResult], retrieval_only: bool) -> None:
    print("\n" + "=" * 66)
    print(f"  {'metric':22} {'value':>8}")
    print("-" * 66)
    # A metric that was not measured must not print as 0% -- that reads as a
    # total failure rather than "this run did not exercise it".
    answer_metrics = {"term_accuracy", "faithfulness", "refusal_accuracy"}
    for key in (
        "recall_at_5",
        "recall_at_10",
        "mrr",
        "term_accuracy",
        "faithfulness",
        "refusal_accuracy",
    ):
        if retrieval_only and key in answer_metrics:
            print(f"  {key:22} {'not run':>8}")
        else:
            print(f"  {key:22} {summary[key]:>8.2%}")
    print(f"  {'median latency':22} {summary['median_latency_ms']:>7}ms")
    print("=" * 66)

    problems = [
        r
        for r in results
        # Refusal cases have no expected source by definition, so a "miss" is
        # meaningless for them.
        if (r.retrieved and not r.hit_at_5 and not r.should_refuse)
        or r.faithful == "unfaithful"
        or r.term_ok is False
        or r.refusal_correct is False
    ]
    if problems:
        print("\nFailing cases:")
        for r in problems:
            reasons = []
            if r.retrieved and not r.hit_at_5:
                reasons.append("retrieval miss")
            if r.term_ok is False:
                reasons.append(f"missing {r.terms_missing}")
            if r.faithful == "unfaithful":
                reasons.append(f"unfaithful: {r.faithfulness_reason[:70]}")
            if r.refusal_correct is False:
                reasons.append(
                    "answered when it should have refused"
                    if r.should_refuse
                    else "refused a question the corpus can answer"
                )
            print(f"  - {r.id}: {'; '.join(reasons)}")


def check_against_baseline(summary: dict[str, Any], retrieval_only: bool) -> int:
    """Compare against the committed baseline, skipping unmeasured metrics.

    A retrieval-only run does not produce answer metrics, and comparing their
    zeros against a baseline reports a catastrophic regression that did not
    happen -- which would fail every pull request and train everyone to ignore
    the gate. Only metrics this run actually measured are compared.
    """
    if not BASELINE.exists():
        print("\nNo baseline committed; nothing to compare against.")
        return 0

    answer_metrics = {"faithfulness", "term_accuracy", "refusal_accuracy"}
    base = json.loads(BASELINE.read_text())["summary"]
    print("\nAgainst baseline:")
    failed = False
    for metric, tol in TOLERANCE.items():
        if retrieval_only and metric in answer_metrics:
            print(f"  {metric:22} {'not measured in this run':>30}")
            continue
        now, was = summary.get(metric, 0.0), base.get(metric, 0.0)
        delta = now - was
        flag = ""
        if delta < -tol:
            flag, failed = "  REGRESSION", True
        print(f"  {metric:22} {was:>7.2%} -> {now:>7.2%}  ({delta:+.2%}){flag}")

    if failed:
        print(
            "\nQuality regressed beyond tolerance. If this is intentional, "
            "re-run with --save and commit the new baseline."
        )
        return 1
    print("\nNo regression.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", action="store_true", help="write results and update baseline")
    parser.add_argument("--check", action="store_true", help="fail on regression vs baseline")
    parser.add_argument("--retrieval-only", action="store_true", help="skip all LLM calls")
    args = parser.parse_args()

    if not args.retrieval_only and not settings.OPENAI_API_KEY:
        print("OPENAI_API_KEY is not set. Use --retrieval-only to run without it.")
        return 2

    print(
        f"Running {len(load_cases())} cases "
        f"({'retrieval only' if args.retrieval_only else 'retrieval + answers'})\n"
    )
    results = asyncio_run(run(args.retrieval_only))
    summary = summarise(results)
    report(summary, results, args.retrieval_only)

    if args.save:
        RESULTS_DIR.mkdir(exist_ok=True)
        payload = {
            "git_sha": _git_sha(),
            "model": settings.CHAT_MODEL,
            "embedding_model": settings.EMBEDDING_MODEL,
            "summary": summary,
            "cases": [asdict(r) for r in results],
        }
        (RESULTS_DIR / f"{_git_sha()}.json").write_text(json.dumps(payload, indent=2))
        if args.retrieval_only and BASELINE.exists():
            # Preserve answer metrics this run did not measure.
            merged = json.loads(BASELINE.read_text())["summary"] | {
                k: v
                for k, v in summary.items()
                if k not in {"faithfulness", "term_accuracy", "refusal_accuracy"}
            }
        else:
            merged = summary
        BASELINE.write_text(json.dumps({"summary": merged, "git_sha": _git_sha()}, indent=2))
        print(f"\nSaved results and updated baseline ({_git_sha()}).")

    return check_against_baseline(summary, args.retrieval_only) if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
