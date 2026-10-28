import os
import re
import json
from typing import List, Dict, Any, Optional
from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="Code Detective - Pure Python AI Engine")

DECISION_KEYWORDS = re.compile(
    r"\b(because|due to|retry|limit|timeout|workaround|prevent|avoid|migrate|deprecate|"
    r"incident|outage|revert|override|supersede|flag|enable|disable|keep|must|hotfix)\b",
    re.IGNORECASE,
)

class ExtractRequest(BaseModel):
    commit_message: str
    files_changed: List[str]

class SynthesizeRequest(BaseModel):
    question: str
    evidence_chain: List[Dict[str, Any]]

@app.post("/ai/extract-decision")
def extract_decision(req: ExtractRequest):
    msg = req.commit_message.strip()
    if not DECISION_KEYWORDS.search(msg) and len(msg.splitlines()) < 2:
        return {"has_decision": False, "summary": "", "rationale": "", "reverses_previous": False}

    api_key = os.getenv("OPENAI_API_KEY")
    reverses = bool(re.search(r"\b(revert|override|replace|instead of|supersede|reduce|lower|change from)\b", msg, re.I))

    if not api_key:
        first_line = msg.splitlines()[0].strip()
        return {
            "has_decision": True,
            "summary": first_line[:160],
            "rationale": msg[:600],
            "reverses_previous": reverses
        }

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=os.getenv("OPENAI_BASE_URL"))
    prompt = f"""Extract the engineering decision from this commit message.
Files: {req.files_changed}
Commit: {msg}
Return JSON: {{"has_decision": bool, "summary": str, "rationale": str, "reverses_previous": bool}}"""
    try:
        resp = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        return json.loads(resp.choices[0].message.content)
    except Exception:
        return {
            "has_decision": True,
            "summary": msg.splitlines()[0][:160],
            "rationale": msg[:600],
            "reverses_previous": reverses
        }

@app.post("/ai/synthesize-answer")
def synthesize_answer(req: SynthesizeRequest):
    if not req.evidence_chain:
        return {
            "answer": "REFUSED: The Neo4j knowledge graph contains no verified commit, PR, incident, or design-doc evidence governing this question. Refusing to speculate.",
            "status": "INSUFFICIENT_EVIDENCE"
        }

    primary = req.evidence_chain[0]
    confidence = float(primary.get("confidence", 0.0))
    if confidence < 0.35:
        return {
            "answer": f"REFUSED (Low Confidence {confidence:.2f}): Insufficient graph evidence linking to an authoritative engineering decision.",
            "status": "INSUFFICIENT_EVIDENCE"
        }

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        lines = [
            f"**Active Decision ({primary.get('decisionId')})**: {primary.get('summary')}",
            f"**Reasoning / Context**: {primary.get('rationale')}",
            f"**Author (Person to Ask)**: {primary.get('authorName')} <{primary.get('authorEmail')}> on {str(primary.get('date'))[:10]} (Commit `{str(primary.get('commitSha'))[:8]}`)"
        ]
        if primary.get("incidentId"):
            lines.append(f"**Linked Incident**: `{primary.get('incidentId')}`")
        superseded = primary.get("supersededDecisions", [])
        if superseded:
            old_str = "; ".join(f"'{s.get('summary')}' ({str(s.get('date'))[:10]} by {s.get('authorName')})" for s in superseded)
            lines.append(f"**Supersession Alert**: This decision overrode earlier decision(s): {old_str}")
        drifted = primary.get("driftedDocs", [])
        if drifted:
            lines.append(f"**Documentation Drift Alert**: Design doc(s) {drifted} are older than this commit and contain stale information.")
        return {"answer": "\n".join(lines), "status": "ANSWERED"}

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=os.getenv("OPENAI_BASE_URL"))
    prompt = f"""Answer the developer's 'Why' question strictly using this Neo4j GraphRAG evidence.
Highlight the active decision, commit SHA, date, person to ask, any incident ID, and explicitly warn if older decisions were superseded or if documentation has drifted.
Question: {req.question}
Evidence: {json.dumps(req.evidence_chain, indent=2)}"""
    resp = client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )
    return {"answer": resp.choices[0].message.content.strip(), "status": "ANSWERED"}
