package com.codedetective.controller;

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
