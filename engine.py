"""OpportunityMap core engine.

No Streamlit imports here, so everything is easy to test.

Design rule: the AI (or the rule-based fallback) only EXTRACTS facts from the
transcript. Plain Python does all the ROI math, so every number is traceable
and editable by a human.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

import pandas as pd
import requests
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# What Dafinitiq can sell, mapped to keywords, typical automation rate, effort
# --------------------------------------------------------------------------
SERVICES = {
    "Email Routing & Triage": {
        "offering": "AI Automation Bots",
        "label": "Manual email sorting and forwarding",
        "keywords": ["email", "inbox"],
        "automatable": 70, "effort": 2,
    },
    "Document Processing": {
        "offering": "AI Document Processing (OCR + LLM)",
        "label": "Reading documents and pulling out the details",
        "keywords": ["invoice", "pdf", "contract", "receipt", "paper form",
                     "forms", "paperwork", "purchase order"],
        "automatable": 80, "effort": 3,
    },
    "Data Entry & Reporting": {
        "offering": "AI Automation Bots",
        "label": "Manual data entry and report building",
        "keywords": ["spreadsheet", "data entry", "retype", "copied", "by hand",
                     "manually", "crm", "export", "report"],
        "automatable": 75, "effort": 2,
    },
    "Lead Follow-up & Qualification": {
        "offering": "AI Automation Bots + Chatbots",
        "label": "Chasing and qualifying incoming leads",
        "keywords": ["lead", "follow", "chasing", "prospect", "inquiries", "enquiries"],
        "automatable": 65, "effort": 2,
    },
    "Appointment Booking": {
        "offering": "Chatbots & Voice Agents",
        "label": "Booking, rescheduling and reminding by hand",
        "keywords": ["appointment", "booking", "reschedul", "no-show", "calendar",
                     "schedul", "showing"],
        "automatable": 70, "effort": 2,
    },
    "Phone & Voice Agent": {
        "offering": "Voice Agents",
        "label": "Answering repetitive phone calls",
        "keywords": ["phone", "calls", "call back", "voicemail", "answering"],
        "automatable": 60, "effort": 4,
    },
    "Customer Support Chatbot": {
        "offering": "Hilda / Virtual Assistants",
        "label": "Answering the same customer questions",
        "keywords": ["where is my order", "return", "same questions", "tracking",
                     "refund", "faq"],
        "automatable": 70, "effort": 2,
    },
    "Personalization & Recommendations": {
        "offering": "Personalization Engines",
        "label": "One-size-fits-all marketing",
        "keywords": ["recommend", "personali", "newsletter", "upsell", "campaign"],
        "automatable": 40, "effort": 4,
    },
}

EFFORT_LABEL = {2: "Low", 3: "Medium", 4: "High"}
DEFAULT_HOURS = 2.0          # used (and flagged as an assumption) when the client gave no number
INTERVIEWER = {"engineer", "interviewer", "dafinitiq", "consultant"}
OWNER_RE = re.compile(
    r"\b(receptionist|front desk|office manager|coordinator|bookkeeper|accountant|"
    r"agents|assistant|admin|sales team|support team)\b", re.I)


# --------------------------------------------------------------------------
# Data models
# --------------------------------------------------------------------------
class Opportunity(BaseModel):
    task: str
    service: str
    owner: str = "Not specified"
    evidence: str
    hours_per_week: Optional[float] = None
    assumed: bool = False
    quote_verified: bool = True


class Analysis(BaseModel):
    client_summary: str
    opportunities: List[Opportunity]
    mode: str
    notes: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def parse_turns(transcript: str):
    """Return (speaker, text) for every client turn. Interviewer turns are skipped."""
    turns = []
    for line in transcript.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z][A-Za-z .]{1,29}):\s*(.+)$", line)
        if m:
            speaker, text = m.group(1).strip(), m.group(2).strip()
            if speaker.lower() in INTERVIEWER:
                continue
            turns.append((speaker, text))
        else:
            turns.append(("Client", line))
    return turns


def hours_per_week_from_text(text: str) -> Optional[float]:
    """Find a stated time cost in the client's words. Returns None if nothing stated."""
    t = text.lower().replace("an hour", "1 hour")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|minutes?|mins?)\s*(?:a|per|each|every)\s*(day|week)", t)
    if m:
        val, unit, per = float(m.group(1)), m.group(2), m.group(3)
        hrs = val if unit.startswith("h") else val / 60
        return round(hrs * (5 if per == "day" else 1), 2)
    c = re.search(r"(\d+)\s+(?:[a-z\-]+\s+){0,3}?(?:a|per|each|every)\s+(day|week)", t)
    e = re.search(r"(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?)\s*(?:each|apiece)", t)
    if c and e:
        count = float(c.group(1)) * (5 if c.group(2) == "day" else 1)
        per_item = float(e.group(1)) / (60 if e.group(2).startswith("m") else 1)
        return round(count * per_item, 2)
    return None


def best_category(text: str) -> Optional[str]:
    t = text.lower()
    best, best_hits = None, 0
    for name, svc in SERVICES.items():
        hits = sum(1 for kw in svc["keywords"] if kw in t)
        if hits > best_hits:
            best, best_hits = name, hits
    return best


def _find_owner(text: str) -> str:
    m = OWNER_RE.search(text)
    return m.group(1).title() if m else "Not specified"


# --------------------------------------------------------------------------
# Extraction: rule-based fallback (works with no API key)
# --------------------------------------------------------------------------
def analyze_rules(transcript: str) -> Analysis:
    turns = parse_turns(transcript)
    if not turns:
        return Analysis(client_summary="No client speech found.", opportunities=[], mode="Demo mode (rule-based)")
    summary = turns[0][1]
    opps: List[Opportunity] = []
    for _, text in turns[1:]:
        cat = best_category(text)
        if not cat:
            continue
        hrs = hours_per_week_from_text(text)
        evidence = text if len(text) <= 300 else _best_sentence(text, cat)
        opps.append(Opportunity(
            task=SERVICES[cat]["label"], service=cat, owner=_find_owner(text),
            evidence=evidence, hours_per_week=hrs if hrs is not None else DEFAULT_HOURS,
            assumed=hrs is None, quote_verified=True))
    return Analysis(client_summary=summary, opportunities=opps, mode="Demo mode (rule-based)")


def _best_sentence(text: str, cat: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kws = SERVICES[cat]["keywords"]
    return max(sentences, key=lambda s: sum(1 for k in kws if k in s.lower()))


# --------------------------------------------------------------------------
# Extraction: AI mode (open Llama model through Groq's OpenAI-compatible API)
# --------------------------------------------------------------------------
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


def _system_prompt() -> str:
    names = ", ".join(f'"{n}"' for n in SERVICES)
    return (
        "You are an analyst at an AI automation agency. You read a discovery-call "
        "transcript and EXTRACT manual, repetitive tasks the client describes.\n"
        "Return ONLY a JSON object with this shape:\n"
        '{"client_summary": "max 2 sentences",\n'
        ' "opportunities": [{"task": "short description", '
        f'"service": one of [{names}], '
        '"owner": "who does it, or Not specified", '
        '"evidence": "an EXACT verbatim quote copied from the client, max 250 characters", '
        '"hours_per_week": number or null}]}\n'
        "Rules: never invent facts. Only set hours_per_week if the client stated or clearly "
        "implied a time cost (convert days to weeks using 5 working days); otherwise null. "
        "Copy quotes exactly. Skip anything that is not a manual, repetitive task."
    )


def analyze_llm(transcript: str, api_key: str, model: str = GROQ_MODEL) -> Analysis:
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model, "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": transcript[:24000]},
            ],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = json.loads(resp.json()["choices"][0]["message"]["content"])
    norm_transcript = _norm(transcript)
    opps: List[Opportunity] = []
    for item in data.get("opportunities", []):
        evidence = str(item.get("evidence", "")).strip()
        service = item.get("service")
        if service not in SERVICES:
            service = best_category(evidence + " " + str(item.get("task", ""))) or "Data Entry & Reporting"
        hrs = item.get("hours_per_week")
        try:
            hrs = float(hrs) if hrs is not None else None
        except (TypeError, ValueError):
            hrs = None
        if hrs is not None:
            hrs = max(0.0, min(hrs, 168.0))
        opps.append(Opportunity(
            task=str(item.get("task") or SERVICES[service]["label"])[:120],
            service=service, owner=str(item.get("owner") or "Not specified"),
            evidence=evidence, hours_per_week=hrs if hrs is not None else DEFAULT_HOURS,
            assumed=hrs is None,
            quote_verified=bool(evidence) and _norm(evidence) in norm_transcript))
    return Analysis(client_summary=str(data.get("client_summary", ""))[:500],
                    opportunities=opps, mode=f"AI mode ({model} via Groq)")


def analyze(transcript: str, use_ai: bool = False, api_key: Optional[str] = None) -> Analysis:
    """Try AI mode first if requested; fall back to rules if anything goes wrong."""
    notes: List[str] = []
    if use_ai and api_key:
        try:
            return analyze_llm(transcript, api_key)
        except Exception as exc:  # network, bad key, bad JSON...
            notes.append(f"AI mode failed ({type(exc).__name__}); used the rule-based fallback instead.")
    result = analyze_rules(transcript)
    result.notes = notes
    return result


# --------------------------------------------------------------------------
# ROI math (plain Python, no AI)
# --------------------------------------------------------------------------
def to_dataframe(analysis: Analysis) -> pd.DataFrame:
    rows = []
    for o in analysis.opportunities:
        svc = SERVICES[o.service]
        rows.append({
            "Task": o.task, "Service": o.service, "Dafinitiq offering": svc["offering"],
            "Owner": o.owner, "Hours/week": o.hours_per_week,
            "Automatable %": svc["automatable"], "Assumed": o.assumed,
            "Quote verified": o.quote_verified, "Evidence": o.evidence,
        })
    return pd.DataFrame(rows)


def compute(df: pd.DataFrame, hourly_cost: float) -> pd.DataFrame:
    """annual savings = hours/week x 52 x hourly cost x automatable %.
    priority score = annual savings / effort (cheap wins first)."""
    out = df.copy()
    if out.empty:
        return out
    effort = out["Service"].map(lambda s: SERVICES[s]["effort"])
    out["Hours saved/yr"] = (out["Hours/week"] * 52 * out["Automatable %"] / 100).round(0)
    out["Annual savings ($)"] = (out["Hours saved/yr"] * hourly_cost).round(0)
    out["Effort"] = effort.map(EFFORT_LABEL)
    out["_score"] = out["Annual savings ($)"] / effort
    out = out.sort_values("_score", ascending=False).reset_index(drop=True)
    out.insert(0, "Priority", range(1, len(out) + 1))
    return out.drop(columns=["_score"])


def report_markdown(summary: str, results: pd.DataFrame, hourly_cost: float, mode: str) -> str:
    total = int(results["Annual savings ($)"].sum()) if not results.empty else 0
    hours = int(results["Hours saved/yr"].sum()) if not results.empty else 0
    lines = [
        "# Automation Opportunity Map", "",
        f"**Client summary:** {summary}", "",
        f"**Estimated total:** about {hours:,} hours and ${total:,} saved per year "
        f"(at ${hourly_cost:g}/hour).", "",
        "## Ranked opportunities", "",
        "| # | Opportunity | Suggested solution | Hours/week | Saved/yr | Effort |",
        "|---|---|---|---|---|---|",
    ]
    for _, r in results.iterrows():
        flag = " *(assumed)*" if r["Assumed"] else ""
        lines.append(f"| {r['Priority']} | {r['Task']} ({r['Service']}) | {r['Dafinitiq offering']} | "
                     f"{r['Hours/week']:g}{flag} | ${int(r['Annual savings ($)']):,} | {r['Effort']} |")
    lines += ["", "## Evidence from the call", ""]
    for _, r in results.iterrows():
        v = "" if r["Quote verified"] else " (quote could not be verified in the transcript)"
        lines.append(f"**{r['Priority']}. {r['Task']}** - owner: {r['Owner']}{v}")
        lines.append(f"> {r['Evidence']}")
        lines.append("")
    lines += [
        "## How the numbers are calculated", "",
        "Annual savings = hours per week x 52 x hourly cost x share that can be automated. "
        "Items marked *(assumed)* had no time stated on the call; a default was used and should be confirmed with the client. "
        "Priority = annual savings divided by implementation effort.", "",
        f"_Generated by OpportunityMap - {mode}. Draft for engineer review._",
    ]
    return "\n".join(lines)
