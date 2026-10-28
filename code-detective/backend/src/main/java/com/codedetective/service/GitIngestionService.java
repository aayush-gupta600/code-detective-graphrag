package com.codedetective.service;

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

    private static final Pattern PR_REGEX = Pattern.compile("(?:PR\\s*#|pull request\\s*#|\\(#)(\\d+)\\)?", Pattern.CASE_INSENSITIVE);
    private static final Pattern ISSUE_REGEX = Pattern.compile("(?:fixes|closes|resolves|issue|refs)\\s*#(\\d+)", Pattern.CASE_INSENSITIVE);
    private static final Pattern INCIDENT_REGEX = Pattern.compile("\\b(INC-\\d+|SEV-[123]-\\d+|OUTAGE-\\d+|INCIDENT-\\d+)\\b", Pattern.CASE_INSENSITIVE);
    private static final Pattern CONFIG_REGEX = Pattern.compile("^([A-Z][A-Z0-9_]{2,}|[a-z0-9_.-]+\\.(?:retry|limit|timeout|enabled|flag)[a-z0-9_.-]*)\\s*[:=]\\s*(.+)$", Pattern.CASE_INSENSITIVE);
    private static final Pattern METHOD_REGEX = Pattern.compile("(?:def|function|public|private|protected)\\s+(?:[\\w<>\\[\\]]+\\s+)?(\\w+)\\s*\\(");

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
                String relPath = repoRoot.relativize(filePath).toString().replace("\\", "/");
                fileCount++;
                neo4jClient.query("MERGE (:File {path: $path})").bind(relPath).to("path").run();

                List<String> lines;
                String content;
                try {
                    lines = Files.readAllLines(filePath);
                    content = String.join("\n", lines);
                } catch (Exception e) {
                    // Skip binary or unreadable files gracefully
                    continue;
                }

                // Extract Functions / Methods
                if (relPath.endsWith(".java") || relPath.endsWith(".py") || relPath.endsWith(".js") || relPath.endsWith(".ts")) {
                    for (String line : lines) {
                        Matcher m = METHOD_REGEX.matcher(line.trim());
                        if (m.find()) {
                            String name = m.group(1);
                            if (!Set.of("if", "for", "while", "switch", "catch").contains(name)) {
                                entityCount++;
                                neo4jClient.query("""
                                    MERGE (e:CodeEntity {id: $id})
                                    SET e.name = $name, e.file_path = $path
                                    WITH e MATCH (f:File {path: $path}) MERGE (f)-[:CONTAINS]->(e)
                                    """)
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
                            neo4jClient.query("""
                                MERGE (k:ConfigKey {key: $key})
                                SET k.value = $val, k.file_path = $path
                                WITH k MATCH (f:File {path: $path}) MERGE (f)-[:DEFINES_CONFIG]->(k)
                                """)
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
                    neo4jClient.query("""
                        MERGE (d:DesignDoc {path: $path})
                        SET d.content = $content, d.last_modified_date = $lastMod
                        WITH d
                        OPTIONAL MATCH (k:ConfigKey) WHERE $content CONTAINS k.key
                        FOREACH (_ IN CASE WHEN k IS NOT NULL THEN [1] ELSE [] END | MERGE (d)-[:DOCUMENTS]->(k))
                        WITH d
                        OPTIONAL MATCH (e:CodeEntity) WHERE $content CONTAINS e.name
                        FOREACH (_ IN CASE WHEN e IS NOT NULL THEN [1] ELSE [] END | MERGE (d)-[:DOCUMENTS]->(e))
                        """)
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
                neo4jClient.query("""
                    MERGE (p:Person {email: $email}) SET p.name = $name
                    MERGE (c:Commit {sha: $sha})
                    SET c.message = $msg, c.date = $date, c.timestamp = $ts
                    MERGE (p)-[:AUTHORED]->(c)
                    """)
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

                    neo4jClient.query("""
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
                        """)
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
                        neo4jClient.query("""
                            MATCH (d:Decision {id: $decId})
                            MATCH (f:File {path: $fpath})
                            MERGE (d)-[:GOVERNS]->(f)
                            WITH d, f
                            OPTIONAL MATCH (f)-[:CONTAINS]->(e:CodeEntity) WHERE $msg CONTAINS e.name
                            FOREACH (_ IN CASE WHEN e IS NOT NULL THEN [1] ELSE [] END | MERGE (d)-[:GOVERNS]->(e))
                            WITH d, f
                            OPTIONAL MATCH (f)-[:DEFINES_CONFIG]->(k:ConfigKey) WHERE $msg CONTAINS k.key
                            FOREACH (_ IN CASE WHEN k IS NOT NULL THEN [1] ELSE [] END | MERGE (d)-[:GOVERNS]->(k))
                            """)
                                .bind(decId).to("decId")
                                .bind(fpath).to("fpath")
                                .bind(msg).to("msg")
                                .run();
                    }

                    // Detect & Link Supersession in Neo4j
                    Long superseded = neo4jClient.query("""
                        MATCH (new_d:Decision {id: $decId})-[:GOVERNS]->(target)
                        MATCH (old_d:Decision)-[:GOVERNS]->(target)
                        WHERE old_d.id <> new_d.id
                          AND old_d.timestamp < new_d.timestamp
                          AND old_d.status = 'ACTIVE'
                          AND (labels(target)[0] IN ['ConfigKey', 'CodeEntity'] OR $reverses = true)
                        SET old_d.status = 'SUPERSEDED'
                        MERGE (new_d)-[:SUPERSEDES]->(old_d)
                        RETURN count(DISTINCT old_d) AS cnt
                        """)
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