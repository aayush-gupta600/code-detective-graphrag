package com.codedetective.dto;

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
