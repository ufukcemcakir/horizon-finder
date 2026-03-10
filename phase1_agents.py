import os
import re
import json
import time
from typing import Any, Optional

from groq import Groq

from phase1_config import CFG
from phase1_utils import _estimate_tokens, _trim
from phase1_db import load_cached_analyses, save_analysis, save_screening_cache


_RE_CODE_FENCE = re.compile(r"^`{1,3}(?:json)?\s*|\s*`{1,3}$", re.DOTALL)


def _get_groq_client() -> Optional[Groq]:
    key = os.environ.get("GROQ_API_KEY")
    return Groq(api_key=key) if key else None


class _RateLimiter:
    def __init__(self, tpm_limit: int = CFG.GROQ_TPM_LIMIT, rpm_limit: int = CFG.GROQ_RPM_LIMIT):
        self.tpm_limit = tpm_limit
        self.rpm_limit = rpm_limit
        self._token_log: list[tuple[float, int]] = []
        self._request_log: list[float] = []

    def _cleanup(self, now: float) -> None:
        cutoff = now - 60.0
        self._token_log = [(t, c) for t, c in self._token_log if t > cutoff]
        self._request_log = [t for t in self._request_log if t > cutoff]

    def tokens_used_last_minute(self) -> int:
        self._cleanup(time.time())
        return sum(c for _, c in self._token_log)

    def requests_last_minute(self) -> int:
        self._cleanup(time.time())
        return len(self._request_log)

    def wait_if_needed(self, estimated_tokens: int) -> None:
        while True:
            now = time.time()
            self._cleanup(now)
            tokens_used = sum(c for _, c in self._token_log)
            reqs_used = len(self._request_log)

            if tokens_used + estimated_tokens <= self.tpm_limit and reqs_used < self.rpm_limit:
                break

            oldest_token_time = self._token_log[0][0] if self._token_log else now
            oldest_req_time = self._request_log[0] if self._request_log else now
            wait_until = min(oldest_token_time, oldest_req_time) + 60.0
            wait_secs = max(0.5, wait_until - now + 0.5)
            time.sleep(min(wait_secs, 15.0))

    def record(self, token_count: int) -> None:
        now = time.time()
        self._token_log.append((now, token_count))
        self._request_log.append(now)


# Global rate limiter
_rate_limiter = _RateLimiter()


def _llm_call(
    client: Groq,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float = 0.0,
) -> str:
    est_input = _estimate_tokens(system + user)
    est_total = est_input + max_tokens
    _rate_limiter.wait_if_needed(est_total)

    last_error = None
    for attempt in range(CFG.GROQ_MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=CFG.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            usage = getattr(resp, "usage", None)
            if usage:
                actual_tokens = (getattr(usage, "total_tokens", None) or est_total)
            else:
                actual_tokens = est_total
            _rate_limiter.record(actual_tokens)

            return resp.choices[0].message.content.strip()

        except Exception as e:
            last_error = e
            err_str = str(e).lower()
            is_rate_limit = (
                "429" in err_str
                or "rate_limit" in err_str
                or "rate limit" in err_str
                or "too many" in err_str
                or "tokens per minute" in err_str
            )
            if is_rate_limit and attempt < CFG.GROQ_MAX_RETRIES:
                wait = CFG.GROQ_RETRY_BASE_SEC * (2 ** attempt)
                time.sleep(wait)
                _rate_limiter.record(est_total)
                continue
            else:
                raise

    raise last_error


def _parse_json_response(raw: str) -> Any:
    cleaned = _RE_CODE_FENCE.sub("", raw).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```\w*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for pattern in [r'\[[\s\S]*\]', r'\{[\s\S]*\}']:
        match = re.search(pattern, cleaned)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("No valid JSON found in response", cleaned, 0)


_SCREENING_SYSTEM = """You are an expert EU Horizon Europe funding advisor. You will receive an organization profile and a numbered list of Horizon Europe call summaries.

Your task: Select ONLY the calls that have a clear thematic, technical, or capability match with the organization. Consider:
- Domain alignment (e.g., AI company + AI call = match)
- Capability fit (e.g., SME with sensor expertise + IoT call = match)
- Technology readiness level compatibility
- Past project experience relevance

Be selective and precise. Only include calls where there is genuine overlap — not tangential connections.

Respond with ONLY a JSON array of the call numbers (integers) you select. Example: [1, 3, 7]
If no calls match, return: []
Do NOT include explanations, only the JSON array."""


def _build_call_catalog_text(calls_df):
    batches = []
    n = len(calls_df)
    batch_size = CFG.SCREENING_BATCH_SIZE

    for start in range(0, n, batch_size):
        chunk = calls_df.iloc[start:start + batch_size]
        lines = []
        tids = []
        for local_idx, (_, row) in enumerate(chunk.iterrows(), 1):
            title = row.get("title", "")
            summary_text = _trim(
                row.get("call_description", "") or row.get("summary", ""),
                500,
            )
            toa = row.get("type_of_action", "")
            lines.append(
                f"{local_idx}. [{row['topic_id']}] {title}\n"
                f"   Type: {toa}\n"
                f"   Description: {summary_text}"
            )
            tids.append(row["topic_id"])
        batches.append(("\n\n".join(lines), tids))

    return batches


def run_screening_agent(
    active_df, profile_text: str, client: Groq, top_n: int, progress_cb=None,
) -> list[str]:
    batches = _build_call_catalog_text(active_df)
    selected_tids: list[str] = []
    total_batches = len(batches)

    for batch_idx, (catalog_text, batch_tids) in enumerate(batches):
        if progress_cb:
            progress_cb(batch_idx, total_batches)

        user_prompt = (
            f"### ORGANIZATION PROFILE\n{_trim(profile_text, CFG.MAX_PROFILE_CHARS)}\n\n"
            f"### CALLS (batch {batch_idx + 1}/{total_batches}, {len(batch_tids)} calls)\n"
            f"{catalog_text}\n\n"
            f"Select up to {min(top_n, len(batch_tids))} most relevant call numbers from this batch. "
            f"Return ONLY a JSON array of integers."
        )
        try:
            raw = _llm_call(client, _SCREENING_SYSTEM, user_prompt, CFG.LLM_MAX_TOKENS_SCREENING)
            picks = _parse_json_response(raw)
            if isinstance(picks, list):
                for idx in picks:
                    if isinstance(idx, int) and 1 <= idx <= len(batch_tids):
                        selected_tids.append(batch_tids[idx - 1])
        except json.JSONDecodeError:
            pass
        except Exception as e:
            err_str = str(e).lower()
            if "rate" in err_str or "429" in err_str:
                time.sleep(30)

        if batch_idx < total_batches - 1:
            time.sleep(CFG.GROQ_DELAY_SEC)

    return selected_tids


_ANALYSIS_SYSTEM = """You are a senior EU funding advisor. Evaluate how well a Horizon Europe call matches an organization's profile.

Consider these factors:
- Domain and technology alignment
- Capability and expertise match
- Past experience relevance
- TRL compatibility
- Strategic fit with organization's interests

Respond with a single valid JSON object only — no markdown, no extra text.

Required schema:
{"score":<int 0-100>,"verdict":"<one sentence>","strengths":["<s1>","<s2>"],"gaps":["<g1>"]}

Scoring guide:
- 80-100: Strong fit — core expertise directly matches call objectives
- 60-79: Good fit — significant overlap with some gaps
- 40-59: Partial fit — some relevant capabilities but major gaps exist
- 20-39: Weak fit — tangential connection only
- 0-19: Not a fit — no meaningful overlap"""


def analyze_call(call_row: dict, profile_text: str, client: Groq) -> dict:
    prompt = (
        f"### ORGANIZATION PROFILE\n{_trim(profile_text, CFG.MAX_PROFILE_CHARS)}\n\n"
        f"### CALL\nTitle: {call_row.get('title', '')}\n"
        f"Topic ID: {call_row.get('topic_id', '')}\n"
        f"Type: {call_row.get('type_of_action', '')}\n"
        f"Description:\n{_trim(call_row.get('call_description', '') or call_row.get('summary', ''))}"
    )
    try:
        raw = _llm_call(client, _ANALYSIS_SYSTEM, prompt, CFG.LLM_MAX_TOKENS_ANALYSIS)
        result = _parse_json_response(raw)
        return {
            "score": int(result.get("score", 0)),
            "verdict": str(result.get("verdict", "")),
            "strengths": result.get("strengths", []),
            "gaps": result.get("gaps", []),
        }
    except json.JSONDecodeError as e:
        return {"score": -1, "verdict": f"JSON parse error: {e}", "strengths": [], "gaps": []}
    except Exception as e:
        return {"score": -1, "verdict": f"LLM error: {e}", "strengths": [], "gaps": []}


def run_analysis_agent(
    candidates_df, profile_text: str, phash: str, client: Groq, progress_cb=None,
) -> 'object':
    import pandas as pd

    candidates = candidates_df.copy()
    topic_ids = candidates["topic_id"].tolist()
    cached = load_cached_analyses(phash, topic_ids)
    to_fetch = [tid for tid in topic_ids if tid not in cached]
    total = len(to_fetch)
    results: dict[str, dict] = {}

    for i, tid in enumerate(to_fetch):
        if progress_cb:
            progress_cb(i, total, tid)
        row = candidates[candidates["topic_id"] == tid].iloc[0]
        outcome = analyze_call(row, profile_text, client)
        save_analysis(
            phash, tid, outcome["score"], outcome["verdict"],
            json.dumps(outcome["strengths"]), json.dumps(outcome["gaps"]),
        )
        results[tid] = outcome
        if i < total - 1:
            time.sleep(CFG.GROQ_DELAY_SEC)

    all_results = {**cached, **results}

    for v in all_results.values():
        for key in ("strengths", "gaps"):
            if isinstance(v[key], str):
                try:
                    v[key] = json.loads(v[key])
                except Exception:
                    v[key] = [v[key]] if v[key] else []

    candidates["llm_score"] = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("score", -1))
    candidates["verdict"] = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("verdict", ""))
    candidates["strengths"] = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("strengths", []))
    candidates["gaps"] = candidates["topic_id"].map(lambda t: all_results.get(t, {}).get("gaps", []))

    return candidates.sort_values("llm_score", ascending=False).reset_index(drop=True)


def groq_contribution_idea(call_row: dict, profile_text: str) -> tuple[str, str]:
    client = _get_groq_client()
    if not client:
        return "", "GROQ_API_KEY not set. Please export it and restart."
    prompt = (
        "You are an expert EU proposal writer.\n\n"
        f"### CALL\nTitle: {call_row.get('title', '')}\n"
        f"Description: {_trim(call_row.get('call_description', ''), CFG.MAX_DESC_CHARS)}\n"
        f"Type: {call_row.get('type_of_action', '')}\n"
        f"### ORGANIZATION PROFILE\n{_trim(profile_text, CFG.MAX_PROFILE_CHARS)}\n\n"
        "Write a contribution idea with sections:\n"
        "1) Understanding of the Call\n2) Relevance of the Company (the reason why the company is a good fit for the call or not)\n"
        "3) Proposed Technical Contributions (focusing on the usability, effectiveness and feasibility of the solution)\n"
        "4) Requirements (partners, data sources, hardware)"
    )
    try:
        content = _llm_call(
            client, "You are an expert in EU research proposal writing.",
            prompt, CFG.LLM_MAX_TOKENS_IDEA, temperature=0.25,
        )
        return prompt, content
    except Exception as e:
        return prompt, f"LLM error: {e}"
