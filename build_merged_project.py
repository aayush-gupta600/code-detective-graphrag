from pathlib import Path

PROJECT_FILES = {
    # =========================================================================
    # 1. PURE PYTHON AI ENGINE (ONLY LLM EXTRACTION & SYNTHESIS - NO DB/BACKEND)
    # =========================================================================
    "code-detective/ai-engine/requirements.txt": """fastapi>=0.110.0
uvicorn>=0.28.0
pydantic>=2.6.0
openai>=1.14.0
python-dotenv>=1.0.1
""",

    "code-detective/ai-engine/ai_service.py": """import os
import re
import json
from typing import List, Dict, Any, Optional
from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="Code Detective - Pure Python AI Engine")

DECISION_KEYWORDS = re.compile(
    r"\\b(because|due to|retry|limit|timeout|workaround|prevent|avoid|migrate|deprecate|"
    r"incident|outage|revert|override|supersede|flag|enable|disable|keep|must|hotfix)\\b",
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
    reverses = bool(re.search(r"\\b(revert|override|replace|instead of|supersede|reduce|lower|change from)\\b", msg, re.I))

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
    prompt = f\"\"\"Extract the engineering decision from this commit message.
Files: {req.files_changed}
Commit: {msg}
Return JSON: {{"has_decision": bool, "summary": str, "rationale": str, "reverses_previous": bool}}\"\"\"
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
        return {"answer": "\\n".join(lines), "status": "ANSWERED"}

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=os.getenv("OPENAI_BASE_URL"))
    prompt = f\"\"\"Answer the developer's 'Why' question strictly using this Neo4j GraphRAG evidence.
Highlight the active decision, commit SHA, date, person to ask, any incident ID, and explicitly warn if older decisions were superseded or if documentation has drifted.
Question: {req.question}
Evidence: {json.dumps(req.evidence_chain, indent=2)}\"\"\"
    resp = client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )
    return {"answer": resp.choices[0].message.content.strip(), "status": "ANSWERED"}
""",

    # =========================================================================
    # 2. SPRING BOOT BACKEND (ALL GIT PARSING, NEO4J GRAPH DB, CYPHER & APIS)
    # =========================================================================
    "code-detective/backend/pom.xml": """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.3.4</version>
        <relativePath/>
    </parent>

    <groupId>com.codedetective</groupId>
    <artifactId>backend</artifactId>
    <version>1.0.0</version>
    <name>code-detective-backend</name>

    <properties>
        <java.version>21</java.version>
    </properties>

    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-data-neo4j</artifactId>
        </dependency>
        <!-- Eclipse JGit so Spring Boot parses Git history natively in Java -->
        <dependency>
            <groupId>org.eclipse.jgit</groupId>
            <artifactId>org.eclipse.jgit</artifactId>
            <version>6.9.0.202403050737-r</version>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-test</artifactId>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
        </plugins>
    </build>
</project>
""",

    "code-detective/backend/src/main/resources/application.yml": """server:
  port: 8080

spring:
  application:
    name: code-detective-backend
  neo4j:
    uri: ${NEO4J_URI:bolt://localhost:7687}
    authentication:
      username: ${NEO4J_USER:neo4j}
      password: ${NEO4J_PASSWORD:password}

ai:
  python-url: ${PYTHON_AI_URL:http://localhost:8000}
""",

    "code-detective/backend/src/main/java/com/codedetective/CodeDetectiveApplication.java": """package com.codedetective;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class CodeDetectiveApplication {
    public static void main(String[] args) {
        SpringApplication.run(CodeDetectiveApplication.class, args);
    }
}
""",

    "code-detective/backend/src/main/java/com/codedetective/dto/Models.java": """package com.codedetective.dto;

import java.util.List;
import java.util.Map;

public class Models {
    public record IngestRequest(String repoPath, int maxCommits) {}
    public record AskRequest(String question) {}

    public record AiDecisionResponse(
        boolean has_decision,
        String summary,
        String rationale,
        boolean reverses_previous
    ) {}

    public record WhyResponse(
        String answer,
        double confidence,
        String status,
        String activeDecisionId,
        Map<String, String> personToAsk,
        List<Map<String, Object>> supersededDecisions,
        List<String> driftedDocs,
        List<Map<String, Object>> evidenceChain
    ) {}
}
""",

    "code-detective/backend/src/main/java/com/codedetective/service/PythonAiBridge.java": """package com.codedetective.service;

import com.codedetective.dto.Models.AiDecisionResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

@Service
public class PythonAiBridge {

    private final RestClient restClient;
    private static final Pattern DECISION_KW = Pattern.compile(
        "\\\\b(because|due to|retry|limit|timeout|workaround|prevent|avoid|migrate|deprecate|incident|outage|revert|override|supersede|flag|enable|disable|keep|must|hotfix)\\\\b",
        Pattern.CASE_INSENSITIVE
    );
    private static final Pattern REVERSE_KW = Pattern.compile(
        "\\\\b(revert|override|replace|instead of|supersede|reduce|lower|change from)\\\\b",
        Pattern.CASE_INSENSITIVE
    );

    public PythonAiBridge(@Value("${ai.python-url}") String pythonUrl) {
        this.restClient = RestClient.builder().baseUrl(pythonUrl).build();
    }

    public AiDecisionResponse extractDecision(String commitMessage, List<String> filesChanged) {
        try {
            return restClient.post()
                    .uri("/ai/extract-decision")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of("commit_message", commitMessage, "files_changed", filesChanged))
                    .retrieve()
                    .body(AiDecisionResponse.class);
        } catch (Exception e) {
            // Fallback heuristic if Python server is not running
            boolean hasDec = DECISION_KW.matcher(commitMessage).find();
            boolean rev = REVERSE_KW.matcher(commitMessage).find();
            String firstLine = commitMessage.split("\\\\R")[0];
            return new AiDecisionResponse(
                hasDec,
                firstLine.substring(0, Math.min(firstLine.length(), 160)),
                commitMessage.substring(0, Math.min(commitMessage.length(), 600)),
                rev
            );
        }
    }

    @SuppressWarnings("unchecked")
    public Map<String, String> synthesizeAnswer(String question, List<Map<String, Object>> evidenceChain) {
        try {
            return restClient.post()
                    .uri("/ai/synthesize-answer")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(Map.of("question", question, "evidence_chain", evidenceChain))
                    .retrieve()
                    .body(Map.class);
        } catch (Exception e) {
            if (evidenceChain.isEmpty()) {
                return Map.of(
                    "answer", "REFUSED: The Neo4j knowledge graph contains no verified evidence for this query.",
                    "status", "INSUFFICIENT_EVIDENCE"
                );
            }
            Map<String, Object> primary = evidenceChain.get(0);
            double conf = ((Number) primary.getOrDefault("confidence", 0.0)).doubleValue();
            if (conf < 0.35) {
                return Map.of(
                    "answer", "REFUSED (Low Confidence): Insufficient graph evidence.",
                    "status", "INSUFFICIENT_EVIDENCE"
                );
            }
            return Map.of(
                "answer", "**Active Decision (" + primary.get("decisionId") + ")**: " + primary.get("summary")
                        + "\\n**Reasoning**: " + primary.get("rationale"),
                "status", "ANSWERED"
            );
        }
    }
}
""",

    "code-detective/backend/src/main/java/com/codedetective/service/GitIngestionService.java": """package com.codedetective.service;

import com.codedetective.dto.Models.AiDecisionResponse;
import org.eclipse.jgit.api.Git;
import org.eclipse.jgit.diff.DiffEntry;
import org.eclipse.jgit.diff.DiffFormatter;
import org.eclipse.jgit.lib.ObjectId;
import org.eclipse.jgit.lib.Repository;
import org.eclipse.jgit.revwalk.RevCommit;
import org.eclipse.jgit.util.io.DisabledOutputStream;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.stereotype.Service;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

@Service
public class GitIngestionService {

    private final Neo4jClient neo4jClient;
    private final PythonAiBridge aiBridge;

    private static final Pattern PR_REGEX = Pattern.compile("(?:PR\\\\s*#|pull request\\\\s*#|\\\\(#)(\\\\d+)\\\\)?", Pattern.CASE_INSENSITIVE);
    private static final Pattern ISSUE_REGEX = Pattern.compile("(?:fixes|closes|resolves|issue|refs)\\\\s*#(\\\\d+)", Pattern.CASE_INSENSITIVE);
    private static final Pattern INCIDENT_REGEX = Pattern.compile("\\\\b(INC-\\\\d+|SEV-[123]-\\\\d+|OUTAGE-\\\\d+|INCIDENT-\\\\d+)\\\\b", Pattern.CASE_INSENSITIVE);
    private static final Pattern CONFIG_REGEX = Pattern.compile("^([A-Z][A-Z0-9_]{2,}|[a-z0-9_.-]+\\\\.(?:retry|limit|timeout|enabled|flag)[a-z0-9_.-]*)\\\\s*[:=]\\\\s*(.+)$", Pattern.CASE_INSENSITIVE);
    private static final Pattern METHOD_REGEX = Pattern.compile("(?:def|function|public|private|protected)\\\\s+(?:[\\\\w<>\\\\[\\\\]]+\\\\s+)?(\\\\w+)\\\\s*\\\\(");

    public GitIngestionService(Neo4jClient neo4jClient, PythonAiBridge aiBridge) {
        this.neo4jClient = neo4jClient;
        this.aiBridge = aiBridge;
    }

    public Map<String, Object> ingestRepository(String repoPathStr, int maxCommits) throws Exception {
        Path repoRoot = Path.of(repoPathStr).toAbsolutePath().normalize();
        if (!Files.exists(repoRoot.resolve(".git"))) {
            throw new IllegalArgumentException("Not a valid git repository: " + repoRoot);
        }

        int fileCount = 0, entityCount = 0, configCount = 0, docCount = 0;

        // 1. Scan repository files, functions/methods, config keys, and design docs in Java
        try (Stream<Path> paths = Files.walk(repoRoot)) {
            List<Path> allFiles = paths
                .filter(Files::isRegularFile)
                .filter(p -> !p.toString().contains(".git") && !p.toString().contains("node_modules") && !p.toString().contains("target"))
                .toList();

            for (Path filePath : allFiles) {
                String relPath = repoRoot.relativize(filePath).toString().replace("\\\\", "/");
                fileCount++;
                neo4jClient.query("MERGE (:File {path: $path})").bind(relPath).to("path").run();

                List<String> lines = Files.readAllLines(filePath);
                String content = String.join("\\n", lines);

                // Extract Functions / Methods
                if (relPath.endsWith(".java") || relPath.endsWith(".py") || relPath.endsWith(".js") || relPath.endsWith(".ts")) {
                    for (String line : lines) {
                        Matcher m = METHOD_REGEX.matcher(line.trim());
                        if (m.find()) {
                            String name = m.group(1);
                            if (!Set.of("if", "for", "while", "switch", "catch").contains(name)) {
                                entityCount++;
                                neo4jClient.query(\"\"\"
                                    MERGE (e:CodeEntity {id: $id})
                                    SET e.name = $name, e.file_path = $path
                                    WITH e MATCH (f:File {path: $path}) MERGE (f)-[:CONTAINS]->(e)
                                    \"\"\")
                                    .bind(relPath + "::" + name).to("id")
                                    .bind(name).to("name")
                                    .bind(relPath).to("path")
                                    .run();
                            }
                        }
                    }
                }

                // Extract Config Keys
                if (relPath.endsWith(".yml") || relPath.endsWith(".yaml") || relPath.endsWith(".properties") || relPath.endsWith(".env") || relPath.endsWith(".py") || relPath.endsWith(".java")) {
                    for (String line : lines) {
                        Matcher cm = CONFIG_REGEX.matcher(line.trim());
                        if (cm.find()) {
                            String key = cm.group(1).trim();
                            String val = cm.group(2).split("#")[0].trim();
                            configCount++;
                            neo4jClient.query(\"\"\"
                                MERGE (k:ConfigKey {key: $key})
                                SET k.value = $val, k.file_path = $path
                                WITH k MATCH (f:File {path: $path}) MERGE (f)-[:DEFINES_CONFIG]->(k)
                                \"\"\")
                                .bind(key).to("key")
                                .bind(val).to("val")
                                .bind(relPath).to("path")
                                .run();
                        }
                    }
                }

                // Extract Design Docs
                if ((relPath.endsWith(".md") || relPath.endsWith(".rst")) && (relPath.toLowerCase().contains("doc") || relPath.toLowerCase().contains("adr") || relPath.toLowerCase().contains("arch"))) {
                    docCount++;
                    String lastModified = Instant.ofEpochMilli(filePath.toFile().lastModified()).toString();
                    neo4jClient.query(\"\"\"
                        MERGE (d:DesignDoc {path: $path})
                        SET d.content = $content, d.last_modified_date = $lastMod
                        WITH d
                        OPTIONAL MATCH (k:ConfigKey) WHERE $content CONTAINS k.key
                        FOREACH (_ IN CASE WHEN k IS NOT NULL THEN [1] ELSE [] END | MERGE (d)-[:DOCUMENTS]->(k))
                        WITH d
                        OPTIONAL MATCH (e:CodeEntity) WHERE $content CONTAINS e.name
                        FOREACH (_ IN CASE WHEN e IS NOT NULL THEN [1] ELSE [] END | MERGE (d)-[:DOCUMENTS]->(e))
                        \"\"\")
                        .bind(relPath).to("path")
                        .bind(content.substring(0, Math.min(content.length(), 6000))).to("content")
                        .bind(lastModified).to("lastMod")
                        .run();
                }
            }
        }

        // 2. Walk Git Commit History Chronologically (Oldest -> Newest) using JGit
        int commitCount = 0, decisionCount = 0, supersessionCount = 0, incidentCount = 0;
        try (Git git = Git.open(new File(repoRoot.toFile(), ".git"))) {
            Repository repository = git.getRepository();
            List<RevCommit> commits = new ArrayList<>();
            for (RevCommit c : git.log().setMaxCount(maxCommits > 0 ? maxCommits : 300).call()) {
                commits.add(c);
            }
            Collections.reverse(commits); // Oldest first so supersession links build naturally

            for (RevCommit commit : commits) {
                commitCount++;
                String sha = commit.getName();
                String authorName = commit.getAuthorIdent().getName();
                String authorEmail = commit.getAuthorIdent().getEmailAddress();
                long timestamp = commit.getCommitTime();
                String dateIso = Instant.ofEpochSecond(timestamp).toString();
                String msg = commit.getFullMessage().trim();

                List<String> touchedFiles = getFilesInCommit(repository, commit);

                // Save Person & Commit in Neo4j
                neo4jClient.query(\"\"\"
                    MERGE (p:Person {email: $email}) SET p.name = $name
                    MERGE (c:Commit {sha: $sha})
                    SET c.message = $msg, c.date = $date, c.timestamp = $ts
                    MERGE (p)-[:AUTHORED]->(c)
                    \"\"\")
                    .bind(authorEmail).to("email")
                    .bind(authorName).to("name")
                    .bind(sha).to("sha")
                    .bind(msg).to("msg")
                    .bind(dateIso).to("date")
                    .bind(timestamp).to("ts")
                    .run();

                for (String fpath : touchedFiles) {
                    neo4jClient.query("MERGE (f:File {path: $path}) WITH f MATCH (c:Commit {sha: $sha}) MERGE (c)-[:MODIFIED]->(f)")
                        .bind(fpath).to("path").bind(sha).to("sha").run();
                }

                // Link PRs, Issues, Incidents
                Matcher prM = PR_REGEX.matcher(msg);
                while (prM.find()) {
                    int prNum = Integer.parseInt(prM.group(1));
                    neo4jClient.query("MERGE (pr:PullRequest {number: $pr}) WITH pr MATCH (c:Commit {sha: $sha}) MERGE (c)-[:PART_OF_PR]->(pr)")
                        .bind(prNum).to("pr").bind(sha).to("sha").run();
                }
                Matcher issM = ISSUE_REGEX.matcher(msg);
                while (issM.find()) {
                    int issNum = Integer.parseInt(issM.group(1));
                    neo4jClient.query("MERGE (i:Issue {number: $iss}) WITH i MATCH (c:Commit {sha: $sha}) MERGE (c)-[:RESOLVES_ISSUE]->(i)")
                        .bind(issNum).to("iss").bind(sha).to("sha").run();
                }
                Matcher incM = INCIDENT_REGEX.matcher(msg);
                while (incM.find()) {
                    String incId = incM.group(1).toUpperCase();
                    incidentCount++;
                    neo4jClient.query("MERGE (inc:Incident {id: $inc}) WITH inc MATCH (c:Commit {sha: $sha}) MERGE (c)-[:FIXES_INCIDENT]->(inc)")
                        .bind(incId).to("inc").bind(sha).to("sha").run();
                }

                // Call Python AI Engine to extract Decision JSON
                AiDecisionResponse aiDec = aiBridge.extractDecision(msg, touchedFiles);
                if (aiDec != null && aiDec.has_decision()) {
                    decisionCount++;
                    String decId = "DEC-" + sha.substring(0, 8);

                    neo4jClient.query(\"\"\"
                        MATCH (c:Commit {sha: $sha})
                        MATCH (p:Person {email: $email})
                        MERGE (d:Decision {id: $decId})
                        SET d.summary = $summary, d.rationale = $rationale,
                            d.date = $date, d.timestamp = $ts, d.status = 'ACTIVE'
                        MERGE (d)-[:RECORDED_IN]->(c)
                        MERGE (p)-[:MADE_DECISION]->(d)
                        WITH d, c
                        OPTIONAL MATCH (c)-[:PART_OF_PR]->(pr:PullRequest)
                        FOREACH (_ IN CASE WHEN pr IS NOT NULL THEN [1] ELSE [] END | MERGE (d)-[:DISCUSSED_IN]->(pr))
                        WITH d, c
                        OPTIONAL MATCH (c)-[:FIXES_INCIDENT]->(inc:Incident)
                        FOREACH (_ IN CASE WHEN inc IS NOT NULL THEN [1] ELSE [] END | MERGE (d)-[:CAUSED_BY_INCIDENT]->(inc))
                        \"\"\")
                        .bind(sha).to("sha")
                        .bind(authorEmail).to("email")
                        .bind(decId).to("decId")
                        .bind(aiDec.summary()).to("summary")
                        .bind(aiDec.rationale()).to("rationale")
                        .bind(dateIso).to("date")
                        .bind(timestamp).to("ts")
                        .run();

                    // Link Decision to governed Files, ConfigKeys, and CodeEntities
                    for (String fpath : touchedFiles) {
                        neo4jClient.query(\"\"\"
                            MATCH (d:Decision {id: $decId})
                            MATCH (f:File {path: $fpath})
                            MERGE (d)-[:GOVERNS]->(f)
                            WITH d, f
                            OPTIONAL MATCH (f)-[:CONTAINS]->(e:CodeEntity) WHERE $msg CONTAINS e.name
                            FOREACH (_ IN CASE WHEN e IS NOT NULL THEN [1] ELSE [] END | MERGE (d)-[:GOVERNS]->(e))
                            WITH d, f
                            OPTIONAL MATCH (f)-[:DEFINES_CONFIG]->(k:ConfigKey) WHERE $msg CONTAINS k.key
                            FOREACH (_ IN CASE WHEN k IS NOT NULL THEN [1] ELSE [] END | MERGE (d)-[:GOVERNS]->(k))
                            \"\"\")
                            .bind(decId).to("decId")
                            .bind(fpath).to("fpath")
                            .bind(msg).to("msg")
                            .run();
                    }

                    // Detect & Link Supersession in Neo4j
                    Long superseded = neo4jClient.query(\"\"\"
                        MATCH (new_d:Decision {id: $decId})-[:GOVERNS]->(target)
                        MATCH (old_d:Decision)-[:GOVERNS]->(target)
                        WHERE old_d.id <> new_d.id
                          AND old_d.timestamp < new_d.timestamp
                          AND old_d.status = 'ACTIVE'
                          AND (labels(target)[0] IN ['ConfigKey', 'CodeEntity'] OR $reverses = true)
                        SET old_d.status = 'SUPERSEDED'
                        MERGE (new_d)-[:SUPERSEDES]->(old_d)
                        RETURN count(DISTINCT old_d) AS cnt
                        \"\"\")
                        .bind(decId).to("decId")
                        .bind(aiDec.reverses_previous()).to("reverses")
                        .fetchAs(Long.class)
                        .one()
                        .orElse(0L);
                    supersessionCount += superseded.intValue();
                }
            }
        }

        return Map.of(
            "files", fileCount,
            "codeEntities", entityCount,
            "configKeys", configCount,
            "designDocs", docCount,
            "commits", commitCount,
            "decisions", decisionCount,
            "supersessions", supersessionCount,
            "incidents", incidentCount
        );
    }

    private List<String> getFilesInCommit(Repository repo, RevCommit commit) {
        List<String> files = new ArrayList<>();
        try (DiffFormatter df = new DiffFormatter(DisabledOutputStream.INSTANCE)) {
            df.setRepository(repo);
            ObjectId parentId = commit.getParentCount() > 0 ? commit.getParent(0).getTree() : null;
            List<DiffEntry> diffs = df.scan(parentId, commit.getTree());
            for (DiffEntry entry : diffs) {
                String path = entry.getNewPath().equals("/dev/null") ? entry.getOldPath() : entry.getNewPath();
                files.add(path);
            }
        } catch (Exception ignored) {}
        return files;
    }
}
""",

    "code-detective/backend/src/main/java/com/codedetective/service/Neo4jGraphService.java": """package com.codedetective.service;

import com.codedetective.dto.Models.WhyResponse;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.stereotype.Service;

import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class Neo4jGraphService {

    private final Neo4jClient neo4jClient;
    private final PythonAiBridge aiBridge;

    private static final Set<String> STOPWORDS = Set.of(
        "why", "is", "the", "this", "that", "like", "what", "how", "who", "when",
        "was", "were", "are", "in", "on", "to", "for", "of", "and", "or", "a", "an",
        "set", "still", "never", "code", "service", "function", "method", "file"
    );
    private static final Pattern TOKEN_PATTERN = Pattern.compile("[A-Za-z0-9_.\\\\-]+");

    public Neo4jGraphService(Neo4jClient neo4jClient, PythonAiBridge aiBridge) {
        this.neo4jClient = neo4jClient;
        this.aiBridge = aiBridge;
    }

    public List<String> extractKeywords(String question) {
        if (question == null) return List.of();
        List<String> kws = new ArrayList<>();
        Matcher m = TOKEN_PATTERN.matcher(question.toLowerCase());
        while (m.find()) {
            String t = m.group();
            if (t.length() > 1 && !STOPWORDS.contains(t)) kws.add(t);
        }
        return kws;
    }

    @SuppressWarnings("unchecked")
    public WhyResponse answerQuestion(String question) {
        List<String> keywords = extractKeywords(question);
        if (keywords.isEmpty()) {
            return new WhyResponse("REFUSED: No queryable entities or keywords found.", 0.0, "INSUFFICIENT_EVIDENCE", null, null, List.of(), List.of(), List.of());
        }

        String cypher = \"\"\"
            MATCH (d:Decision)-[:RECORDED_IN]->(cm:Commit)<-[:AUTHORED]-(p:Person)
            OPTIONAL MATCH (d)-[:GOVERNS]->(target)
            OPTIONAL MATCH (d)-[:DISCUSSED_IN]->(pr:PullRequest)
            OPTIONAL MATCH (d)-[:CAUSED_BY_INCIDENT]->(inc:Incident)
            OPTIONAL MATCH (d)-[:SUPERSEDES*1..5]->(old_d:Decision)-[:RECORDED_IN]->(old_cm:Commit)<-[:AUTHORED]-(old_p:Person)
            OPTIONAL MATCH (doc:DesignDoc)-[:DOCUMENTS]->(target)
            WHERE doc IS NULL OR doc.last_modified_date < d.date

            WITH d, cm, p, pr, inc,
                 collect(DISTINCT coalesce(target.key, target.name, target.path)) AS targets,
                 collect(DISTINCT CASE WHEN old_d IS NOT NULL THEN {
                     decisionId: old_d.id,
                     summary: old_d.summary,
                     rationale: old_d.rationale,
                     date: old_d.date,
                     sha: old_cm.sha,
                     authorName: old_p.name
                 } END) AS rawSuperseded,
                 collect(DISTINCT doc.path) AS rawDriftedDocs

            WITH d, cm, p, pr, inc, targets,
                 [x IN rawSuperseded WHERE x IS NOT NULL] AS supersededDecisions,
                 [x IN rawDriftedDocs WHERE x IS NOT NULL] AS driftedDocs,
                 reduce(score = 0.0, kw IN $keywords |
                     score +
                     CASE WHEN any(t IN targets WHERE toLower(t) CONTAINS kw) THEN 0.45 ELSE 0.0 END +
                     CASE WHEN toLower(d.summary) CONTAINS kw THEN 0.25 ELSE 0.0 END +
                     CASE WHEN toLower(d.rationale) CONTAINS kw THEN 0.20 ELSE 0.0 END +
                     CASE WHEN inc IS NOT NULL AND toLower(inc.id) CONTAINS kw THEN 0.50 ELSE 0.0 END
                 ) AS rawScore

            WHERE rawScore > 0
            RETURN d.id AS decisionId,
                   d.summary AS summary,
                   d.rationale AS rationale,
                   d.status AS status,
                   d.date AS date,
                   cm.sha AS commitSha,
                   p.name AS authorName,
                   p.email AS authorEmail,
                   pr.number AS prNumber,
                   inc.id AS incidentId,
                   targets AS governedTargets,
                   supersededDecisions,
                   driftedDocs,
                   CASE WHEN rawScore > 0.98 THEN 0.98 ELSE round(rawScore * 100.0) / 100.0 END AS confidence
            ORDER BY CASE WHEN d.status = 'ACTIVE' THEN 0 ELSE 1 END, confidence DESC, d.timestamp DESC
            LIMIT 5
            \"\"\";

        List<Map<String, Object>> evidence = new ArrayList<>(
            neo4jClient.query(cypher).bind(keywords).to("keywords").fetch().all()
        );

        Map<String, String> aiResult = aiBridge.synthesizeAnswer(question, evidence);
        if (evidence.isEmpty() || "INSUFFICIENT_EVIDENCE".equals(aiResult.get("status"))) {
            double conf = evidence.isEmpty() ? 0.0 : ((Number) evidence.get(0).getOrDefault("confidence", 0.0)).doubleValue();
            return new WhyResponse(aiResult.get("answer"), conf, "INSUFFICIENT_EVIDENCE", null, null, List.of(), List.of(), List.of());
        }

        Map<String, Object> primary = evidence.get(0);
        double confidence = ((Number) primary.getOrDefault("confidence", 0.0)).doubleValue();
        Map<String, String> person = Map.of(
            "name", String.valueOf(primary.getOrDefault("authorName", "Unknown")),
            "email", String.valueOf(primary.getOrDefault("authorEmail", "unknown"))
        );

        return new WhyResponse(
            aiResult.get("answer"),
            confidence,
            "ANSWERED",
            String.valueOf(primary.get("decisionId")),
            person,
            (List<Map<String, Object>>) primary.getOrDefault("supersededDecisions", List.of()),
            (List<String>) primary.getOrDefault("driftedDocs", List.of()),
            evidence
        );
    }

    public Collection<Map<String, Object>> getGraphStats() {
        return neo4jClient.query("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY count DESC").fetch().all();
    }

    public Collection<Map<String, Object>> getDecisionTimeline(String target) {
        return neo4jClient.query(\"\"\"
            MATCH (p:Person)-[:MADE_DECISION]->(d:Decision)-[:RECORDED_IN]->(c:Commit)
            OPTIONAL MATCH (d)-[:GOVERNS]->(t)
            OPTIONAL MATCH (d)-[:SUPERSEDES]->(old:Decision)
            WHERE toLower(coalesce(t.key, t.name, t.path, '')) CONTAINS toLower($target)
               OR toLower(d.summary) CONTAINS toLower($target)
            RETURN d.id AS decisionId, d.summary AS summary, d.status AS status,
                   d.date AS date, c.sha AS commitSha, p.name AS author,
                   collect(DISTINCT old.id) AS overrides
            ORDER BY d.timestamp DESC
            \"\"\").bind(target).to("target").fetch().all();
    }
}
""",

    "code-detective/backend/src/main/java/com/codedetective/controller/DetectiveController.java": """package com.codedetective.controller;

import com.codedetective.dto.Models.*;
import com.codedetective.service.GitIngestionService;
import com.codedetective.service.Neo4jGraphService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Collection;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/detective")
@CrossOrigin(origins = "*")
public class DetectiveController {

    private final GitIngestionService ingestionService;
    private final Neo4jGraphService graphService;

    public DetectiveController(GitIngestionService ingestionService, Neo4jGraphService graphService) {
        this.ingestionService = ingestionService;
        this.graphService = graphService;
    }

    @PostMapping("/ingest")
    public ResponseEntity<Map<String, Object>> ingest(@RequestBody IngestRequest req) throws Exception {
        return ResponseEntity.ok(ingestionService.ingestRepository(req.repoPath(), req.maxCommits()));
    }

    @PostMapping("/ask")
    public ResponseEntity<WhyResponse> ask(@RequestBody AskRequest req) {
        return ResponseEntity.ok(graphService.answerQuestion(req.question()));
    }

    @GetMapping("/stats")
    public ResponseEntity<Collection<Map<String, Object>>> stats() {
        return ResponseEntity.ok(graphService.getGraphStats());
    }

    @GetMapping("/history/{target}")
    public ResponseEntity<Collection<Map<String, Object>>> history(@PathVariable String target) {
        return ResponseEntity.ok(graphService.getDecisionTimeline(target));
    }
}
"""
}
for rel_path, content in PROJECT_FILES.items():
    p = Path(rel_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    print(f"Created {rel_path}")

print("\\nUnified project 'code-detective/' created successfully!")