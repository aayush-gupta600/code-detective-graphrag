# Code Detective: Why The Code Is Like This (GraphRAG on Neo4j AuraDB)

**Build Type:** Applied AI (GraphRAG & Architectural Provenance Engine)

## Overview
A new developer can read code, but they cannot read intent. Keyword and vector search return text snippets that mention a topic, not the authoritative engineering decision governing it. Documentation drifts, decisions get reversed years later, and the links between production incidents, pull requests, and lines of code get lost.

**Code Detective** constructs a multi-hop property graph in **Neo4j AuraDB** and uses **GraphRAG** to answer *'Why is the code like this?'* with deterministic evidence chains, supersession alerts, documentation drift detection, and strict anti-hallucination guardrails.

---

## Core Features
1. **Repository & Governance Graph Ingestion**: Maps File, CodeEntity, ConfigKey, Commit, Person, Decision, PullRequest, Incident, and DesignDoc nodes.
2. **Evidence-Backed Q&A (/api/v1/detective/ask)**: Returns the governing Decision, commit SHA, PR number, linked incident (INC-*), and the **Person to Ask** (
ame + email).
3. **Supersession Detection**: Traverses [:SUPERSEDES*1..5] chains to prioritize ACTIVE decisions over SUPERSEDED ones and warns when an earlier decision was overridden.
4. **Documentation Drift Detection**: Flags DesignDoc nodes whose last_modified_date predates the governing Decision.date.
5. **Confidence Signal & Refusal Guardrail**: Computes a graph-grounded confidence score and strictly refuses (INSUFFICIENT_EVIDENCE) when the graph lacks governing evidence.

---

## Tech Stack
- **Graph Database:** Neo4j AuraDB
- **Backend:** Java 21, Spring Boot 3, Spring Data Neo4j (Neo4jClient), JGit
- **AI Synthesis Layer:** Python AI Bridge + GraphRAG Cypher Retrieval

## API Endpoints
- POST /api/v1/detective/ingest — Ingests a Git repository into Neo4j AuraDB
- POST /api/v1/detective/ask — Answers natural language 'Why' questions with full evidence chains
- GET /api/v1/detective/history/{target} — Returns chronological decision & supersession timeline
- GET /api/v1/detective/stats — Returns node counts across the knowledge graph
