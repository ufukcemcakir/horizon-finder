import os
import re
import json
import time
from typing import Any, Optional

from groq import Groq

from .phase1_config import CFG
from .phase1_utils import _estimate_tokens, _trim
from .phase1_db import load_cached_analyses, save_analysis, save_screening_cache


_RE_CODE_FENCE = re.compile(r"^`{1,3}(?:json)?\s*|\s*`{1,3}$", re.DOTALL)


def _get_groq_client() -> Optional[Groq]:
    key = os.environ.get("GROQ_API_KEY")
    return Groq(api_key=key) if key else None


def _parse_structured_profile(profile_text: str) -> dict:
    """Parse the new JSON-based structured profile, with fallback to plain text."""
    if not profile_text or not profile_text.strip():
        return {}
    
    # Try to parse as JSON
    try:
        data = json.loads(profile_text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Fallback: treat as plain text (old format)
    return {"text": profile_text}


def _format_profile_for_llm(profile_text: str) -> str:
    """Format a profile (structured or plain) into readable text for LLM prompts."""
    if not profile_text or not profile_text.strip():
        return "No profile information provided."
    
    profile = _parse_structured_profile(profile_text)
    
    if not profile or profile == {"text": profile_text}:
        # Plain text format
        return profile.get("text", profile_text)
    
    # Structured format
    lines = []
    if profile.get("org_name"):
        lines.append(f"Organization: {profile['org_name']}")
    if profile.get("org_type"):
        org_types = {
            "research": "Research Institution",
            "sme": "SME",
            "large": "Large Enterprise",
            "nonprofit": "Non-Profit / NGO",
            "government": "Government / Public"
        }
        lines.append(f"Type: {org_types.get(profile['org_type'], profile['org_type'])}")
    if profile.get("competencies"):
        lines.append(f"Core Competencies: {profile['competencies']}")
    if profile.get("past_experiences"):
        lines.append(f"Past Experience & Key Projects: {profile['past_experiences']}")
    if profile.get("technical_expertise"):
        lines.append(f"Technical Expertise: {profile['technical_expertise']}")
    if profile.get("partnerships"):
        lines.append(f"Partnerships & Collaborations: {profile['partnerships']}")
    
    return "\n".join(lines) if lines else "No profile information provided."


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


_SCREENING_SYSTEM = """You are an expert EU Horizon Europe funding advisor with deep knowledge of EU research priorities.

You will receive an organization profile (with details on competencies, experience, and expertise) and a numbered list of Horizon Europe call summaries.

Your task: Select ONLY the calls that have a clear, substantive match with the organization's profile. Consider:

1. **Domain Alignment**: Does the call's research domain match the organization's core competencies?
2. **Technical Capability Fit**: Do the organization's technical expertise and past projects align with what the call requires?
3. **Organization Type Suitability**: Is the organization type (SME, Research, Large Enterprise, etc.) a good fit for this call?
4. **Experience Relevance**: Do past projects demonstrate capabilities relevant to the call's objectives?
5. **Strategic Alignment**: Does the call fit with the organization's stated interests and partnerships?

Selection criteria:
- STRONG MATCH (definitely select): Direct overlap in multiple areas - e.g., AI expertise + AI-focused call, sensor tech SME + IoT call
- GOOD MATCH (likely select): Significant overlap with clear relevance - some gaps acceptable
- WEAK MATCH (do not select): Only tangential connection or fundamental capability gaps

Be conservative and selective. Only include calls where the organization could realistically contribute meaningfully.

Respond with ONLY a valid JSON array of the call numbers (integers) you select. Example: [1, 3, 7]
If no calls match well, return: []
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
            status = row.get("status", "")
            lines.append(
                f"{local_idx}. [{row['topic_id']}] {title}\n"
                f"   Status: {status} | Type: {toa}\n"
                f"   Summary: {summary_text}"
            )
            tids.append(row["topic_id"])
        batches.append(("\n\n".join(lines), tids))

    return batches


def run_screening_agent(
    active_df, profile_text: str, client: Groq, top_n: int, progress_cb=None,
) -> list[str]:
    """Screen active calls against organization profile using LLM."""
    batches = _build_call_catalog_text(active_df)
    selected_tids: list[str] = []
    total_batches = len(batches)
    
    # Format the profile nicely for the LLM
    formatted_profile = _format_profile_for_llm(profile_text)

    for batch_idx, (catalog_text, batch_tids) in enumerate(batches):
        if progress_cb:
            progress_cb(batch_idx, total_batches)

        user_prompt = (
            f"### ORGANIZATION PROFILE\n{_trim(formatted_profile, CFG.MAX_PROFILE_CHARS)}\n\n"
            f"### CALLS (batch {batch_idx + 1}/{total_batches}, {len(batch_tids)} calls)\n"
            f"{catalog_text}\n\n"
            f"Select the most relevant call numbers from this batch (up to {min(top_n, len(batch_tids))}). "
            f"Only include calls where there is genuine, substantive fit with the organization's profile."
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


_ANALYSIS_SYSTEM = """You are a senior EU funding advisor with extensive Horizon Europe experience. Your task is to evaluate how well a specific call matches an organization's capabilities and profile.

Scoring Scale:
- 90-100: Excellent fit - Core expertise directly aligns with call requirements. Organization well-positioned to lead.
- 75-89: Strong fit - Significant capability overlap with most requirements met. Suitable as partner or co-lead.
- 60-74: Good fit - Relevant expertise present but some capability gaps exist. Viable participant.
- 40-59: Partial fit - Limited relevance but some transferable skills. Would need external partners for key roles.
- 20-39: Weak fit - Tangential relevance only, significant capability gaps. Not recommended unless desperate.
- 0-19: Poor fit - No meaningful alignment. Do not pursue.

Evaluation Criteria:
1. **Domain & Technology Alignment**: How closely does the call's research domain match the organization's stated competencies?
2. **Capability Coverage**: What percentage of the call's technical requirements can the organization cover?
3. **Experience Relevance**: Do past projects demonstrate applicable experience?
4. **Organization Type Fit**: Is the organization type suitable for the call's requirements?
5. **Strategic Opportunity**: Does this call represent a good strategic opportunity?

Respond with ONLY a valid JSON object. Do not include markdown, explanations, or extra text.

Schema: {"score":<int 0-100>,"verdict":"<1-2 sentence summary>","strengths":["<strength1>","<strength2>","<strength3>"],"gaps":["<gap1>","<gap2>"]}"""


def analyze_call(call_row: dict, profile_text: str, client: Groq) -> dict:
    """Analyze how well a call matches the organization profile."""
    formatted_profile = _format_profile_for_llm(profile_text)
    
    prompt = (
        f"### ORGANIZATION PROFILE\n{_trim(formatted_profile, CFG.MAX_PROFILE_CHARS)}\n\n"
        f"### CALL TO EVALUATE\n"
        f"Topic ID: {call_row.get('topic_id', '')}\n"
        f"Title: {call_row.get('title', '')}\n"
        f"Status: {call_row.get('status', '')}\n"
        f"Type of Action: {call_row.get('type_of_action', '')}\n"
        f"Deadline: {call_row.get('deadline', '')}\n"
        f"\nCall Description:\n{_trim(call_row.get('call_description', '') or call_row.get('summary', ''), CFG.MAX_DESC_CHARS)}\n\n"
        f"Evaluate this call's suitability for the organization."
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
    """Generate a contribution idea for a call based on organization profile."""
    client = _get_groq_client()
    if not client:
        return "", "GROQ_API_KEY not set. Please export it and restart."
    
    formatted_profile = _format_profile_for_llm(profile_text)
    
    prompt = (
        "You are an expert EU proposal writer and Horizon Europe consultant with 10+ years of experience.\n\n"
        f"### ORGANIZATION PROFILE\n{_trim(formatted_profile, CFG.MAX_PROFILE_CHARS)}\n\n"
        f"### CALL\nTitle: {call_row.get('title', '')}\n"
        f"Topic ID: {call_row.get('topic_id', '')}\n"
        f"Type of Action: {call_row.get('type_of_action', '')}\n"
        f"Description:\n{_trim(call_row.get('call_description', ''), CFG.MAX_DESC_CHARS)}\n\n"
        "Write a concise contribution idea with these sections:\n\n"
        "1) **Understanding of the Call**: Summarize what this call is seeking (2-3 sentences).\n\n"
        "2) **Organization's Relevance**: Explain why this organization is (or is not) a good fit. "
        "Reference specific competencies, past experience, and capabilities (3-4 sentences).\n\n"
        "3) **Proposed Technical Contribution**: Outline what the organization can specifically contribute to this call. "
        "Focus on feasibility and alignment with the organization's proven expertise (3-4 sentences).\n\n"
        "4) **Partnership & Resource Requirements**: Identify what partners, expertise gaps, or resources would be needed "
        "for this organization to successfully participate (2-3 sentences).\n\n"
        "Be realistic and honest about fit. If gaps exist, acknowledge them but show how they could be addressed."
    )
    try:
        content = _llm_call(
            client, "You are an expert in EU research proposal writing and Horizon Europe funding.",
            prompt, CFG.LLM_MAX_TOKENS_IDEA, temperature=0.25,
        )
        return prompt, content
    except Exception as e:
        return prompt, f"LLM error: {e}"
