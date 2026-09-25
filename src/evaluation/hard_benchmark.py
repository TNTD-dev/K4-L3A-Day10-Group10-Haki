"""Hard benchmark: cau hoi khong co title, paraphrase, tra loi bang LLM tu context, co cau hoi khong co dap an.

Bo sung cho test set chinh (Guide quy dinh mau cau hoi co title -> de, baseline cham tran).
Muc tieu: do dung nhu nguoi dung that + do hallucination, co provenance de kiem chung.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from statistics import mean
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from core.config import Settings
from core.utils import normalize_whitespace, now_utc, read_json, write_json
from evaluation.metrics import _judge_answer
from retrieval.index import LocalEmbeddingIndex
from retrieval.llm import build_llm

QUESTION_TYPES = ["summary", "authors", "date"]  # categories: Crossref live khong co du lieu
MAX_TITLE_NGRAM = 4  # cau hoi khong duoc chua >= 4 tu lien tiep cua title
MAX_SUMMARY_NGRAM = 5  # ... hoac >= 5 tu lien tiep cua abstract
IDK = "I don't know"

# Cau hoi trong domain, nghe hop ly nhung KHONG co trong corpus -> dung = tu choi tra loi.
# `key` duoc kiem tra khong xuat hien trong corpus truoc khi dua vao benchmark.
# Near-miss trong domain (RAG/agent cho linh vuc ma corpus khong co) -> de bi du do bịa.
UNANSWERABLE = [
    {"question": "Who wrote the study on a retrieval-augmented assistant that helps tax auditors check invoices against regulations?", "key": "tax"},
    {"question": "When was the paper published about an agent that retrieves maintenance manuals to troubleshoot aircraft engines?", "key": "aircraft"},
    {"question": "What does the study find about using retrieval-augmented language models to answer insurance claim questions?", "key": "insurance"},
    {"question": "Who authored the work on an agentic retrieval system that recommends recipes from nutrition guidelines?", "key": "recipe"},
    {"question": "When was the research published on retrieval-augmented tutoring that grades high-school physics homework?", "key": "physics"},
    {"question": "What does the paper propose for retrieval-augmented agents that negotiate energy prices in smart grids?", "key": "smart grid"},
]
MAX_UNIQUE_CUES = 1  # so tu chi xuat hien trong DUNG paper nay (df=1) duoc phep trong cau hoi
STOPWORDS = set("about which what when where study paper work research propose proposes find finds using based model models language large system systems approach method".split())

TYPE_INSTRUCTIONS = {
    "summary": "Ask what the paper proposes or finds.",
    "authors": "Ask who wrote the paper.",
    "date": "Ask when the paper was published.",
}


class GeneratedQuestion(BaseModel):
    question: str = Field(description="One natural question a researcher would ask")


# ---------------------------------------------------------------- leakage filters
def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", str(text).lower())


def shared_ngram(a: str, b: str, n: int) -> str | None:
    """Tra ve n-gram dau tien a va b cung chua (None neu khong co)."""
    grams = {tuple(_tokens(b)[i : i + n]) for i in range(max(0, len(_tokens(b)) - n + 1))}
    ta = _tokens(a)
    for i in range(max(0, len(ta) - n + 1)):
        if tuple(ta[i : i + n]) in grams:
            return " ".join(ta[i : i + n])
    return None


def document_frequency(papers: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for paper in papers:
        for token in set(_tokens(f"{paper['title']} {paper['summary']}")):
            counts[token] = counts.get(token, 0) + 1
    return counts


def unique_cues(question: str, paper: dict[str, Any], df_counts: dict[str, int]) -> list[str]:
    """Tu trong cau hoi chi xuat hien o DUNG paper nay -> 'chia khoa' lo dap an retrieval."""
    own = set(_tokens(f"{paper['title']} {paper['summary']}"))
    return sorted({t for t in _tokens(question) if len(t) > 3 and t not in STOPWORDS and t in own and df_counts.get(t, 0) == 1})


def leakage_reason(question: str, paper: dict[str, Any], df_counts: dict[str, int] | None = None) -> str | None:
    if paper["paper_id"].lower() in question.lower():
        return "contains DOI"
    if hit := shared_ngram(question, paper["title"], MAX_TITLE_NGRAM):
        return f"title {MAX_TITLE_NGRAM}-gram: '{hit}'"
    if hit := shared_ngram(question, paper["summary"], MAX_SUMMARY_NGRAM):
        return f"abstract {MAX_SUMMARY_NGRAM}-gram: '{hit}'"
    if df_counts is not None and len(cues := unique_cues(question, paper, df_counts)) > MAX_UNIQUE_CUES:
        return f"unique cues {cues}"
    return None


def _reference(paper: dict[str, Any], kind: str) -> str:
    if kind == "authors":
        return paper["authors_joined"]
    if kind == "date":
        return paper["published"]
    sentences = re.split(r"(?<=[.!?])\s+", normalize_whitespace(paper["summary"]))
    return " ".join(sentences[:2])


# ---------------------------------------------------------------- build (1 lan, dong bang)
def build_hard_test_set(df: pd.DataFrame, settings: Settings, output_path, max_attempts: int = 3) -> dict[str, Any]:
    llm = build_llm(settings, temperature=0.0).with_structured_output(GeneratedQuestion)
    papers = df.drop_duplicates("paper_id").sort_values("paper_id").to_dict("records")
    df_counts = document_frequency(papers)
    items, rejected = [], []

    for i, paper in enumerate(papers):
        kind = QUESTION_TYPES[i % len(QUESTION_TYPES)]
        if not _reference(paper, kind).strip():
            rejected.append({"paper_id": paper["paper_id"], "reason": f"no ground truth for {kind}"})
            continue
        feedback = ""
        for attempt in range(1, max_attempts + 1):
            prompt = (
                "You write evaluation questions for a search system over scientific papers.\n"
                f"{TYPE_INSTRUCTIONS[kind]}\n"
                "Write it like a busy practitioner who only VAGUELY remembers the paper: describe the general "
                "application area and the general kind of method in plain everyday words. Rules: no title words, "
                "no phrases copied from the abstract, no names, acronyms, numbers, datasets, countries, languages, "
                "product or tool names, no DOI, do not include the answer. One sentence, English.\n"
                f"{feedback}\nTitle: {paper['title']}\nAbstract: {paper['summary'][:1500]}"
            )
            question = normalize_whitespace(llm.invoke(prompt).question)
            reason = leakage_reason(question, paper, df_counts)
            if reason is None:
                items.append({
                    "id": f"hard_{len(items) + 1:03d}",
                    "question_type": kind,
                    "answerable": True,
                    "question": question,
                    "ground_truth": _reference(paper, kind),
                    "ground_truth_doc_ids": [paper["paper_id"]],
                    "generation_attempts": attempt,
                })
                break
            rejected.append({"paper_id": paper["paper_id"], "attempt": attempt, "question": question, "reason": reason})
            feedback = f"Your previous question was rejected because it reused text ({reason}). Rephrase completely."

    corpus = " ".join(df["text_for_embedding"].astype(str)).lower()
    for item in UNANSWERABLE:
        if re.search(rf"\b{re.escape(item['key'])}\b", corpus):
            rejected.append({"question": item["question"], "reason": f"'{item['key']}' found in corpus -> not unanswerable"})
            continue
        items.append({
            "id": f"hard_{len(items) + 1:03d}",
            "question_type": "unanswerable",
            "answerable": False,
            "question": item["question"],
            "ground_truth": f"{IDK}. The indexed corpus does not contain this information.",
            "ground_truth_doc_ids": [],
        })

    payload = {
        "provenance": {
            "created_at": now_utc().isoformat(),
            "generator": f"{settings.llm_provider}/{settings.model_name}",
            "source_rows": len(papers),
            "leakage_filters": {"title_ngram": MAX_TITLE_NGRAM, "abstract_ngram": MAX_SUMMARY_NGRAM, "doi": True,
                                "max_unique_cues": MAX_UNIQUE_CUES},
            "items_fingerprint": hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()[:16],
        },
        "items": items,
        "rejected": rejected,
    }
    write_json(output_path, payload)
    return payload


# ---------------------------------------------------------------- answer + evaluate
def answer_generative(question: str, settings: Settings, index: LocalEmbeddingIndex, llm) -> dict[str, Any]:
    results = index.search(question, top_k=settings.top_k)  # khong exact lookup: chi vector search
    context = "\n\n".join(f"[{k + 1}] {r.content}" for k, r in enumerate(results))
    prompt = (
        "Answer the question using ONLY the context below. Be concise (one or two sentences). "
        f"If the context does not contain the answer, reply exactly: {IDK}.\n\n"
        f"Context:\n{context}\n\nQuestion: {question}"
    )
    answer = llm.invoke(prompt)
    return {"answer": str(getattr(answer, "content", answer)).strip(), "retrieved_doc_ids": [r.paper_id for r in results]}


def wilson(successes: float, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 0.0]
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def evaluate_hard(settings: Settings, index: LocalEmbeddingIndex, test_path, metrics_path, answers_path) -> dict[str, Any]:
    items = read_json(test_path)["items"]
    llm = build_llm(settings, temperature=0.0)
    answers = []
    for item in items:
        out = answer_generative(item["question"], settings, index, llm)
        ids = out["retrieved_doc_ids"]
        gold = item["ground_truth_doc_ids"]
        rank = next((k + 1 for k, pid in enumerate(ids) if pid in gold), None)
        judge = _judge_answer(settings, item["question"], item["ground_truth"], out["answer"])
        answers.append({
            **item,
            **out,
            "rank": rank,
            "abstained": IDK.lower() in out["answer"].lower(),
            "judge": judge.model_dump(),
        })

    ans = [a for a in answers if a["answerable"]]
    neg = [a for a in answers if not a["answerable"]]
    correct = sum(a["judge"]["correct"] for a in ans)
    abstain = sum(a["abstained"] for a in neg)
    summary = {
        "samples": len(answers),
        "answerable": len(ans),
        "hit_at_1": mean(a["rank"] == 1 for a in ans) if ans else 0.0,
        f"hit_at_{settings.top_k}": mean(a["rank"] is not None for a in ans) if ans else 0.0,
        "mrr": mean(1 / a["rank"] if a["rank"] else 0.0 for a in ans) if ans else 0.0,
        "judge_accuracy": correct / len(ans) if ans else 0.0,
        "judge_accuracy_ci95": wilson(correct, len(ans)),
        "mean_judge_score": mean(a["judge"]["score"] for a in ans) if ans else 0.0,
        "wrong_abstentions": sum(a["abstained"] for a in ans),
        # Silent failure tren cau CO dap an: khong tu choi nhung judge cham sai (vd retrieve nham paper roi tra loi tu tin).
        "confident_wrong_rate": sum(not a["abstained"] and not a["judge"]["correct"] for a in ans) / len(ans) if ans else 0.0,
        "unanswerable": len(neg),
        "abstention_rate": abstain / len(neg) if neg else 0.0,
        "hallucination_rate": (len(neg) - abstain) / len(neg) if neg else 0.0,
        "judge_fallbacks": sum("Fallback" in a["judge"]["reasoning"] for a in answers),
        "by_type": {
            kind: {
                "n": len(group),
                "hit_at_1": mean(a["rank"] == 1 for a in group),
                "judge_accuracy": mean(a["judge"]["correct"] for a in group),
            }
            for kind in QUESTION_TYPES
            if (group := [a for a in ans if a["question_type"] == kind])
        },
    }
    write_json(metrics_path, summary)
    write_json(answers_path, answers)
    return summary
