package com.codedetective.service;

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
        "\\b(because|due to|retry|limit|timeout|workaround|prevent|avoid|migrate|deprecate|incident|outage|revert|override|supersede|flag|enable|disable|keep|must|hotfix)\\b",
        Pattern.CASE_INSENSITIVE
    );
    private static final Pattern REVERSE_KW = Pattern.compile(
        "\\b(revert|override|replace|instead of|supersede|reduce|lower|change from)\\b",
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
            String firstLine = commitMessage.split("\\R")[0];
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
                        + "\n**Reasoning**: " + primary.get("rationale"),
                "status", "ANSWERED"
            );
        }
    }
}
