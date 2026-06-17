"""
Semantic Execution Agent - Phase 2
Handles AI-powered semantic reasoning for non-deterministic rules
Uses structured output for reliable decision extraction
"""

from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, Field
from dataclasses import dataclass
import http.client
import json
import os
import re
import socket
import urllib.error
from urllib.parse import urlparse

from agent_reasoning import AgentReasoningLogger


# ============================================================================
# STRUCTURED OUTPUT MODELS
# ============================================================================

class SemanticAdjudicationPayload(BaseModel):
    """Strict payload for structured semantic reasoning matching user requirement"""
    evaluation_status: Literal["PASSED", "EXCLUSION_ACTIVE", "FAILED"]
    reasoning_trace: str = Field(description="Detailed step-by-step trace of semantic rule evaluation against policy wordings")
    confidence_score: float = Field(ge=0.0, le=1.0, description="Numeric confidence score between 0.0 and 1.0")


class SemanticDecision(BaseModel):
    """Structured output from semantic reasoning"""
    decision: Literal["APPROVED", "REJECTED", "UNCERTAIN"]
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0-1")
    reason: str = Field(description="Human-readable explanation")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="Supporting evidence")
    requires_manual_review: bool = False


class ExclusionAssessment(BaseModel):
    """Structured assessment for exclusion rules"""
    is_excluded: bool
    exclusion_type: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    policy_section_ref: Optional[str] = None


class CoverageAssessment(BaseModel):
    """Structured assessment for coverage validation"""
    is_covered: bool
    coverage_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    requires_verification: bool = False


@dataclass
class SemanticResult:
    """Result from semantic reasoning execution"""
    passed: bool
    confidence: float
    reason: str
    evidence: Dict[str, Any]
    raw_response: str
    requires_manual_review: bool


class SemanticExecutionAgent:
    """
    Semantic Execution Agent
    
    Handles semantic reasoning for rules that cannot be purely deterministic:
    - Exclusion validation (cosmetic, diagnostic-only, maternity)
    - Coverage validation (treatment necessity, medical documentation)
    - Waiting period assessment (PED identification)
    
    Uses structured outputs to ensure reliable decision extraction
    
    Supports:
    - OpenAI GPT-4 Turbo
    - Anthropic Claude
    - Local LLM (llama.cpp server)
    """
    
    # GBNF grammar that forces valid SemanticAdjudicationPayload JSON.
    # llama.cpp will constrain token sampling to this grammar, eliminating
    # markdown wrapping and hallucinated fields entirely (Issue 18).
    _ADJUDICATION_GRAMMAR = r'''
    root   ::= "{" ws "\"evaluation_status\"" ws ":" ws status ws "," ws
                    "\"reasoning_trace\"" ws ":" ws string ws "," ws
                    "\"confidence_score\"" ws ":" ws number ws "}"
    status ::= "\"PASSED\"" | "\"EXCLUSION_ACTIVE\"" | "\"FAILED\""
    string ::= "\"" ([^"\\] | "\\" .)* "\""
    number ::= [0-9]+ ("." [0-9]+)?
    ws     ::= [ \t\n]*
    '''

    def __init__(
        self,
        llm_provider: str = "local",
        confidence_threshold: float = 0.90,
        local_llm_url: str = "http://127.0.0.1:8080",
        reasoning_on: Optional[bool] = None
    ):
        self.llm_provider = llm_provider
        self.confidence_threshold = confidence_threshold
        self.local_llm_url = local_llm_url
        from config import settings
        self.api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")

        if reasoning_on is not None:
            self.reasoning_on = reasoning_on
        else:
            self.reasoning_on = os.getenv("REASONING_ON", "true").lower() in ("true", "1", "yes")

        # Issue 20: split connect vs read timeouts for llama.cpp
        from config import settings
        self.connect_timeout_s: int = settings.llm_connect_timeout_s
        self.read_timeout_s: int = settings.llm_read_timeout_s

        import threading
        self._local_llm_lock = threading.Lock()

        # Track semantic calls for observability
        self.call_count = 0
        self.total_confidence = 0.0
        self._result_cache: Dict[str, SemanticResult] = {}

        # Fast probe to check if legacy raw /completion endpoint is supported and active
        self.use_legacy_completion = False
        if llm_provider == "local":
            try:
                parsed = urlparse(local_llm_url)
                h = parsed.hostname or "127.0.0.1"
                p = parsed.port or 8080
                use_tls = parsed.scheme == "https"
                
                probe_payload = {
                    "prompt": "health",
                    "n_predict": 1,
                    "stream": False
                }
                body = json.dumps(probe_payload).encode("utf-8")
                conn_cls = http.client.HTTPSConnection if use_tls else http.client.HTTPConnection
                conn = conn_cls(h, p, timeout=1.0)
                try:
                    conn.connect()
                    conn.sock.settimeout(1.0)
                    conn.request("POST", "/completion", body=body, headers={
                        "Content-Type": "application/json",
                        "Content-Length": str(len(body))
                    })
                    resp = conn.getresponse()
                    if resp.status == 200:
                        self.use_legacy_completion = True
                finally:
                    conn.close()
            except Exception:
                self.use_legacy_completion = False

        print(f"[AGENT] Semantic Agent initialized with {llm_provider} provider (reasoning_on={self.reasoning_on}, legacy_completion={self.use_legacy_completion})")
        if llm_provider == "local":
            print(f"   Local LLM URL: {local_llm_url}")
    
    def execute_semantic_rule(
        self,
        rule_id: str,
        prompt: str,
        rule_type: Literal["exclusion", "coverage", "waiting_period"],
        claim_id: str = "UNKNOWN_CLAIM",
        line_item_id: Optional[str] = None
    ) -> SemanticResult:
        """
        Execute semantic reasoning for a rule
        
        Args:
            rule_id: Rule identifier
            prompt: Prepared semantic prompt
            rule_type: Type of rule (exclusion, coverage, waiting_period)
            claim_id: Claim ID for logging
            line_item_id: Line Item ID for logging
        
        Returns:
            SemanticResult with structured decision
        """
        cache_key = f"{rule_id}:{rule_type}:{hash(prompt[:500])}"
        if cache_key in self._result_cache:
            return self._result_cache[cache_key]

        self.call_count += 1
        
        # Route to appropriate handler based on rule type
        if rule_type == "exclusion":
            res = self._assess_exclusion(rule_id, prompt, claim_id, line_item_id)
        elif rule_type == "coverage":
            res = self._assess_coverage(rule_id, prompt, claim_id, line_item_id)
        elif rule_type == "waiting_period":
            res = self._assess_waiting_period(rule_id, prompt, claim_id, line_item_id)
        else:
            raise ValueError(f"Unknown rule type: {rule_type}")

        self._result_cache[cache_key] = res
        return res
    
    def _assess_exclusion(self, rule_id: str, prompt: str, claim_id: str, line_item_id: Optional[str]) -> SemanticResult:
        """
        Assess if an exclusion applies
        Uses structured output for reliable extraction
        """
        system_prompt = (
            "You are an expert health insurance adjudicator specializing in policy exclusion assessment.\n\n"
            "RESPONSE FORMAT CONTRACT:\n"
            "If reasoning/thinking is enabled, you may output your thinking process first (using standard thinking tags like <think>...</think> or <|think|>...</|think|>).\n"
            "The final part of your response MUST contain a single raw JSON object. "
            "Do NOT write any other text, explanation, preamble, or markdown outside the JSON (except the thinking tags/blocks if reasoning is enabled).\n"
            "Required schema:\n"
            '{"evaluation_status": "PASSED", "reasoning_trace": "step-by-step analysis", "confidence_score": 0.95}\n\n'
            "evaluation_status token definitions:\n"
            "  PASSED           — exclusion does NOT apply; the claim is allowed through this gate\n"
            "  EXCLUSION_ACTIVE — exclusion applies; the claim must be blocked at this gate\n"
            "  FAILED           — use ONLY when critical data is entirely absent preventing evaluation\n\n"
            "Correct response example (output EXACTLY this structure, with your own content):\n"
            '{"evaluation_status": "PASSED", "reasoning_trace": "The treatment is an appendectomy (active surgery), not a diagnostic-only admission. Exclusion R3_EXCL_004 does not apply.", "confidence_score": 0.96}'
        )
        raw_response = self._call_llm(system_prompt, prompt, SemanticAdjudicationPayload)
        
        try:
            assessment = SemanticAdjudicationPayload.model_validate_json(raw_response)
            passed = assessment.evaluation_status == "PASSED"
            
            # Critical Safety Guardrail: If confidence score is less than 0.90, route to manual review
            requires_review = assessment.confidence_score < self.confidence_threshold or assessment.evaluation_status == "FAILED"
            
            # Accumulate confidence for tracking
            self.total_confidence += assessment.confidence_score
            
            # Log structured agent reasoning
            AgentReasoningLogger.log_llm_call(
                claim_id=claim_id,
                line_item_id=line_item_id,
                rule_id=rule_id,
                provider=self.llm_provider,
                url=self.local_llm_url if self.llm_provider == "local" else None,
                system_prompt=system_prompt,
                user_prompt=prompt,
                raw_response=raw_response,
                structured_output=assessment.model_dump(),
                confidence=assessment.confidence_score,
                requires_manual_review=requires_review
            )
            
            return SemanticResult(
                passed=passed,
                confidence=assessment.confidence_score,
                reason=assessment.reasoning_trace,
                evidence={"evaluation_status": assessment.evaluation_status},
                raw_response=raw_response,
                requires_manual_review=requires_review
            )
        except Exception as e:
            AgentReasoningLogger.log_llm_call(
                claim_id=claim_id,
                line_item_id=line_item_id,
                rule_id=rule_id,
                provider=self.llm_provider,
                url=self.local_llm_url if self.llm_provider == "local" else None,
                system_prompt=system_prompt,
                user_prompt=prompt,
                raw_response=raw_response,
                structured_output={"error": str(e)},
                confidence=0.0,
                requires_manual_review=True
            )
            return SemanticResult(
                passed=False,
                confidence=0.0,
                reason=f"Failed to parse LLM response: {str(e)}",
                evidence={"error": str(e)},
                raw_response=raw_response,
                requires_manual_review=True
            )

    def _assess_coverage(self, rule_id: str, prompt: str, claim_id: str, line_item_id: Optional[str]) -> SemanticResult:
        """Assess if treatment is covered"""
        system_prompt = (
            "You are an expert health insurance adjudicator specializing in coverage validation.\n\n"
            "RESPONSE FORMAT CONTRACT:\n"
            "If reasoning/thinking is enabled, you may output your thinking process first (using standard thinking tags like <think>...</think> or <|think|>...</|think|>).\n"
            "The final part of your response MUST contain a single raw JSON object. "
            "Do NOT write any other text, explanation, preamble, or markdown outside the JSON (except the thinking tags/blocks if reasoning is enabled).\n"
            "Required schema:\n"
            '{"evaluation_status": "PASSED", "reasoning_trace": "step-by-step analysis", "confidence_score": 0.95}\n\n'
            "evaluation_status token definitions:\n"
            "  PASSED           — treatment satisfies coverage criteria; claim passes this gate\n"
            "  EXCLUSION_ACTIVE — treatment fails coverage sublimits or parameters; claim must be blocked\n"
            "  FAILED           — use ONLY when critical data is entirely absent preventing evaluation\n\n"
            "Correct response example (output EXACTLY this structure, with your own content):\n"
            '{"evaluation_status": "PASSED", "reasoning_trace": "Hospitalization exceeds 24 hours and is for active surgical treatment. Coverage criteria are met.", "confidence_score": 0.97}'
        )
        raw_response = self._call_llm(system_prompt, prompt, SemanticAdjudicationPayload)
        
        try:
            assessment = SemanticAdjudicationPayload.model_validate_json(raw_response)
            passed = assessment.evaluation_status == "PASSED"
            
            # Critical Safety Guardrail
            requires_review = assessment.confidence_score < self.confidence_threshold or assessment.evaluation_status == "FAILED"
            
            # Accumulate confidence
            self.total_confidence += assessment.confidence_score
            
            # Log structured agent reasoning
            AgentReasoningLogger.log_llm_call(
                claim_id=claim_id,
                line_item_id=line_item_id,
                rule_id=rule_id,
                provider=self.llm_provider,
                url=self.local_llm_url if self.llm_provider == "local" else None,
                system_prompt=system_prompt,
                user_prompt=prompt,
                raw_response=raw_response,
                structured_output=assessment.model_dump(),
                confidence=assessment.confidence_score,
                requires_manual_review=requires_review
            )
            
            return SemanticResult(
                passed=passed,
                confidence=assessment.confidence_score,
                reason=assessment.reasoning_trace,
                evidence={"evaluation_status": assessment.evaluation_status},
                raw_response=raw_response,
                requires_manual_review=requires_review
            )
        except Exception as e:
            AgentReasoningLogger.log_llm_call(
                claim_id=claim_id,
                line_item_id=line_item_id,
                rule_id=rule_id,
                provider=self.llm_provider,
                url=self.local_llm_url if self.llm_provider == "local" else None,
                system_prompt=system_prompt,
                user_prompt=prompt,
                raw_response=raw_response,
                structured_output={"error": str(e)},
                confidence=0.0,
                requires_manual_review=True
            )
            return SemanticResult(
                passed=False,
                confidence=0.0,
                reason=f"Failed to parse LLM response: {str(e)}",
                evidence={"error": str(e)},
                raw_response=raw_response,
                requires_manual_review=True
            )

    def _assess_waiting_period(self, rule_id: str, prompt: str, claim_id: str, line_item_id: Optional[str]) -> SemanticResult:
        """Assess waiting period applicability"""
        system_prompt = (
            "You are an expert health insurance adjudicator specializing in waiting period assessment.\n\n"
            "RESPONSE FORMAT CONTRACT:\n"
            "If reasoning/thinking is enabled, you may output your thinking process first (using standard thinking tags like <think>...</think> or <|think|>...</|think|>).\n"
            "The final part of your response MUST contain a single raw JSON object. "
            "Do NOT write any other text, explanation, preamble, or markdown outside the JSON (except the thinking tags/blocks if reasoning is enabled).\n"
            "Required schema:\n"
            '{"evaluation_status": "PASSED", "reasoning_trace": "step-by-step analysis", "confidence_score": 0.95}\n\n'
            "evaluation_status token definitions:\n"
            "  PASSED           — waiting period has been served or is not applicable; claim passes this gate\n"
            "  EXCLUSION_ACTIVE — waiting period is still actively running; claim must be blocked\n"
            "  FAILED           — use ONLY when timeline data is entirely absent preventing evaluation\n\n"
            "Correct response example (output EXACTLY this structure, with your own content):\n"
            '{"evaluation_status": "PASSED", "reasoning_trace": "Policy inception was 2022-01-01. Claim date is 2025-06-01. The 36-month PED waiting period has been fully served.", "confidence_score": 0.98}'
        )
        raw_response = self._call_llm(system_prompt, prompt, SemanticAdjudicationPayload)
        
        try:
            assessment = SemanticAdjudicationPayload.model_validate_json(raw_response)
            passed = assessment.evaluation_status == "PASSED"
            
            # Critical Safety Guardrail
            requires_review = assessment.confidence_score < self.confidence_threshold or assessment.evaluation_status == "FAILED"
            
            # Accumulate confidence
            self.total_confidence += assessment.confidence_score
            
            # Log structured agent reasoning
            AgentReasoningLogger.log_llm_call(
                claim_id=claim_id,
                line_item_id=line_item_id,
                rule_id=rule_id,
                provider=self.llm_provider,
                url=self.local_llm_url if self.llm_provider == "local" else None,
                system_prompt=system_prompt,
                user_prompt=prompt,
                raw_response=raw_response,
                structured_output=assessment.model_dump(),
                confidence=assessment.confidence_score,
                requires_manual_review=requires_review
            )
            
            return SemanticResult(
                passed=passed,
                confidence=assessment.confidence_score,
                reason=assessment.reasoning_trace,
                evidence={"evaluation_status": assessment.evaluation_status},
                raw_response=raw_response,
                requires_manual_review=requires_review
            )
        except Exception as e:
            AgentReasoningLogger.log_llm_call(
                claim_id=claim_id,
                line_item_id=line_item_id,
                rule_id=rule_id,
                provider=self.llm_provider,
                url=self.local_llm_url if self.llm_provider == "local" else None,
                system_prompt=system_prompt,
                user_prompt=prompt,
                raw_response=raw_response,
                structured_output={"error": str(e)},
                confidence=0.0,
                requires_manual_review=True
            )
            return SemanticResult(
                passed=False,
                confidence=0.0,
                reason=f"Failed to parse LLM response: {str(e)}",
                evidence={"error": str(e)},
                raw_response=raw_response,
                requires_manual_review=True
            )
    
    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel]
    ) -> str:
        """
        Call LLM with structured output
        
        Routes to appropriate provider:
        - local: llama.cpp server on localhost:8080
        - openai: OpenAI GPT-4 Turbo
        """
        if self.llm_provider == "local":
            return self._call_local_llm(system_prompt, user_prompt, response_model)
        
        elif self.llm_provider == "openai" and self.api_key:
            return self._call_openai_structured(system_prompt, user_prompt, response_model)
        
        else:
            raise ValueError(
                f"Unsupported or unconfigured LLM provider: {self.llm_provider}. "
                f"Ensure appropriate API keys or servers are active."
            )
    
    def _call_openai_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel]
    ) -> str:
        """
        Call OpenAI with structured outputs (Function Calling)
        Requires openai>=1.0.0
        """
        try:
            import openai
            
            client = openai.OpenAI(api_key=self.api_key)
            
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.0,  # Zero temperature for greedy decoding
                seed=42
            )
            
            return response.choices[0].message.content
        
        except Exception as e:
            print(f"[ERROR] OpenAI API call failed: {e}")
            raise RuntimeError(f"OpenAI API call failed: {e}") from e
    
    def _call_local_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[BaseModel],
    ) -> str:
        """
        Call local llama.cpp server.

        Strategy:
        1. Try /completion (raw text endpoint) using the custom gemma4-e4b-qat
           reasoning chat template format to preserve byte-level prompt constraints
           and enable reasoning-by-default capabilities.
        2. On failure, fall back to /v1/chat/completions (OpenAI-compatible chat
           endpoint) as a robust secondary path.

        Timeouts: 5s connect, 120s read via http.client.
        n_predict=512: enough for the short JSON response.
        """
        with self._local_llm_lock:
            parsed = urlparse(self.local_llm_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 8080
            use_tls = parsed.scheme == "https"

            if self.use_legacy_completion:
                # ------------------------------------------------------------------ #
                # Path 1: /completion (gemma4-e4b-qat reasoning chat template)        #
                # ------------------------------------------------------------------ #
                gemma_prompt = (
                    f"<|turn>system\n"
                    f"<|think|>\n"
                    f"{system_prompt}<turn|>\n"
                    f"<|turn>user\n"
                    f"{user_prompt}<turn|>\n"
                    f"<|turn>model\n"
                )
                legacy_payload = {
                    "prompt": gemma_prompt,
                    "temperature": 0.0,
                    "seed": 42,
                    "top_p": 0.9,
                    "n_predict": 8196,
                    "stop": ["</s>", "<end_of_turn>", "<|eot_id|>", "<turn|>"],
                    "stream": False,
                }
                if not self.reasoning_on:
                    legacy_payload["grammar"] = self._ADJUDICATION_GRAMMAR  # GBNF forces valid schema

                try:
                    content = self._http_post(
                        host, port, use_tls, "/completion", legacy_payload
                    )
                    result = json.loads(content)
                    if "content" in result:
                        raw = result["content"].strip()
                        # Ensure the JSON object is closed if truncated by grammar/stop tokens
                        if raw and not raw.endswith("}"):
                            raw += "}"
                        return self._extract_json_from_response(raw)
                    raise ValueError("Invalid /completion response: no 'content' key")
                except Exception as legacy_err:
                    print(f"[WARN] /completion path failed: {legacy_err}. Trying /v1/chat/completions...")

            # ------------------------------------------------------------------ #
            # Path 2: /v1/chat/completions (OpenAI-compatible fallback)          #
            # ------------------------------------------------------------------ #
            chat_payload = {
                "model": "local",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
                "seed": 42,
                "max_tokens": 8196,
                "stream": False,
            }
            if not self.reasoning_on:
                chat_payload["response_format"] = {"type": "json_object"}

            content = self._http_post(
                host, port, use_tls, "/v1/chat/completions", chat_payload
            )
            result = json.loads(content)
            if "choices" in result and result["choices"]:
                return self._extract_json_from_response(
                    result["choices"][0]["message"]["content"]
                )
            raise ValueError("Unexpected /v1/chat/completions response shape")

    def _http_post(
        self,
        host: str,
        port: int,
        use_tls: bool,
        path: str,
        payload: Dict[str, Any],
    ) -> str:
        """
        Issue 20: HTTP POST with split connect + read timeouts via http.client.
        urllib.request.urlopen accepts only a single timeout that covers both
        phases; using http.client directly allows a short connect timeout (5s,
        fast-fails if llama.cpp is not up) and a long read timeout (120s, gives
        the model time to generate without blocking the event loop forever).
        """
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        conn_cls = http.client.HTTPSConnection if use_tls else http.client.HTTPConnection
        # socket.create_connection respects the timeout as the connect timeout;
        # after connection is established we reset the socket timeout to the
        # longer read timeout.
        conn = conn_cls(host, port, timeout=self.connect_timeout_s)
        try:
            try:
                conn.connect()  # raises socket.timeout on connect failure
            except (socket.timeout, ConnectionRefusedError, OSError) as connect_err:
                raise RuntimeError(
                    f"Cannot connect to local LLM at {host}:{port} — "
                    f"ensure llama.cpp server is running. Error: {connect_err}"
                ) from connect_err
            conn.sock.settimeout(self.read_timeout_s)  # extend timeout for model generation
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
            if resp.status not in (200, 201):
                raise ValueError(f"HTTP {resp.status} from {path}: {resp.read(256)!r}")
            return resp.read().decode("utf-8")
        finally:
            conn.close()
    
    def _extract_json_from_response(self, text: str) -> str:
        """
        Extract JSON object from LLM response text
        
        Handles cases where LLM wraps JSON in markdown or adds extra text
        
        Args:
            text: Raw LLM response
        
        Returns:
            Clean JSON string
        """
        text = text.strip()
        
        # Strip thinking process blocks first if present (helpful for reasoning models)
        import re
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        text = re.sub(r'<\|think\|>.*?</\|think\|>', '', text, flags=re.DOTALL)
        text = re.sub(r'<\|think\|>.*?<\|turn\|>', '<|turn|>', text, flags=re.DOTALL)
        text = re.sub(r'<\|think\|>.*?<turn\|>', '<turn|>', text, flags=re.DOTALL)
        text = re.sub(r'<\|?channel\|?>thought.*?</?\|?channel\|?>', '', text, flags=re.DOTALL)
        
        # Clean up unclosed thinking blocks if they exist at the start
        if '<think>' in text and '</think>' not in text:
            first_brace = text.find("{")
            first_think = text.find("<think>")
            if first_brace > first_think:
                text = text[first_brace:]
        if '<|think|>' in text and '</|think|>' not in text:
            first_brace = text.find("{")
            first_think = text.find("<|think|>")
            if first_brace > first_think:
                text = text[first_brace:]
        if '<|channel>thought' in text and '<channel|>' not in text:
            first_brace = text.find("{")
            first_think = text.find("<|channel>thought")
            if first_brace > first_think:
                text = text[first_brace:]
        
        # Strip any leading thinking tags or unclosed thinking blocks before the JSON object
        first_brace = text.find("{")
        if first_brace > 0:
            pre_text = text[:first_brace]
            if any(marker in pre_text for marker in ['think', 'thought', 'channel', '<', '>', '|']):
                text = text[first_brace:]

        text = text.strip()
        
        # Remove markdown code blocks if present
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        
        if text.endswith("```"):
            text = text[:-3]
        
        text = text.strip()
        
        # Find JSON object boundaries
        start = text.find("{")
        end = text.rfind("}") + 1
        
        if start == -1 or end == 0:
            raise ValueError(f"No JSON object found in response: {text[:200]}")
        
        json_str = text[start:end]
        
        # Validate it's parseable JSON
        try:
            json.loads(json_str)
            return json_str
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in response: {e}")
    
    

    
    def get_average_confidence(self) -> float:
        """Get average confidence across all semantic calls"""
        if self.call_count == 0:
            return 1.0
        return self.total_confidence / self.call_count
