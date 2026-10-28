package com.codedetective.service;

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
    private static final Pattern TOKEN_PATTERN = Pattern.compile("[A-Za-z0-9_.\\-]+");

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

        String cypher = """
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
            """;

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
        return neo4jClient.query("""
            MATCH (p:Person)-[:MADE_DECISION]->(d:Decision)-[:RECORDED_IN]->(c:Commit)
            OPTIONAL MATCH (d)-[:GOVERNS]->(t)
            OPTIONAL MATCH (d)-[:SUPERSEDES]->(old:Decision)
            WHERE toLower(coalesce(t.key, t.name, t.path, '')) CONTAINS toLower($target)
               OR toLower(d.summary) CONTAINS toLower($target)
            WITH d, c, p, collect(DISTINCT old.id) AS overrides
            ORDER BY d.timestamp DESC
            RETURN d.id AS decisionId, d.summary AS summary, d.status AS status,
                   d.date AS date, c.sha AS commitSha, p.name AS author,
                   overrides
            """).bind(target).to("target").fetch().all();
    }
}