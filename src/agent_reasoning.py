"""
Agent Reasoning Logger for Claims Auto-Adjudication Engine
Implements clean, structured tracking of LLM decisions, prompts, tool execution, and routing events.
Logs are persisted to agent_reasoning.log in the workspace root.
"""

import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Resolve the log file path to the project root
WORKSPACE_ROOT = Path(__file__).parent.parent
LOG_FILE_PATH = WORKSPACE_ROOT / "agent_reasoning.log"

class AgentReasoningLogger:
    """
    Structured logger for LLM reasoning, prompts, raw responses,
    tool inputs/outputs, and pipeline routing events.
    """
    _logger: Optional[logging.Logger] = None

    @classmethod
    def _initialize_logger(cls) -> None:
        """Initializes the dedicated Python logger for agent reasoning."""
        if cls._logger is not None:
            return

        logger = logging.getLogger("agent_reasoning_tracker")
        logger.setLevel(logging.INFO)
        logger.propagate = False

        # Clear existing handlers to prevent duplicate logging
        if logger.handlers:
            logger.handlers.clear()

        # File Handler (persisting to workspace root)
        file_handler = logging.FileHandler(str(LOG_FILE_PATH), encoding="utf-8")
        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        cls._logger = logger

    @classmethod
    def get_logger(cls) -> logging.Logger:
        """Returns the initialized logger instance."""
        cls._initialize_logger()
        assert cls._logger is not None
        return cls._logger

    @classmethod
    def _default_serializer(cls, obj: Any) -> Any:
        """Custom JSON serializer for date, datetime, Pydantic models, and custom classes."""
        from datetime import date, datetime
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if hasattr(obj, "model_dump"):
            return getattr(obj, "model_dump")()
        if hasattr(obj, "dict"):
            return getattr(obj, "dict")()
        if hasattr(obj, "__dict__"):
            return getattr(obj, "__dict__")
        if hasattr(obj, "_asdict"):
            return getattr(obj, "_asdict")()
        if isinstance(obj, set):
            return list(obj)
        try:
            return str(obj)
        except Exception:
            return repr(obj)

    @classmethod
    def log_section(cls, title: str) -> None:
        """Logs a clear separator section header."""
        logger = cls.get_logger()
        separator = "=" * 80
        logger.info(f"\n{separator}\n{title.upper():^80}\n{separator}")

    @classmethod
    def log_endorsement(
        cls,
        claim_id: str,
        endorsement_type: str,
        effective_date: str,
        mutations: Dict[str, Any]
    ) -> None:
        """Logs mid-term endorsement mutations applied to the context."""
        logger = cls.get_logger()
        log_payload = {
            "event": "ENDORSEMENT_APPLIED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "claim_id": claim_id,
            "endorsement_type": endorsement_type,
            "effective_date": effective_date,
            "mutations": mutations
        }
        logger.info(f"ENDORSEMENT | Claim: {claim_id} | Type: {endorsement_type} | Date: {effective_date}\n"
                    f"{json.dumps(log_payload, indent=2, default=cls._default_serializer)}")

    @classmethod
    def log_mutual_exclusivity(
        cls,
        claim_id: str,
        gate: str,
        constraints: List[Dict[str, Any]],
        violation: Optional[Dict[str, Any]] = None
    ) -> None:
        """Logs mutual exclusivity constraint evaluation results."""
        logger = cls.get_logger()
        log_payload = {
            "event": "MUTUAL_EXCLUSIVITY_VALIDATION",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "claim_id": claim_id,
            "gate": gate,
            "constraints_evaluated": constraints,
            "violation_detected": violation
        }
        status = "VIOLATION_DETECTED" if violation else "PASSED"
        logger.info(f"MUTUAL_EXCLUSIVITY | Claim: {claim_id} | Gate: {gate} | Status: {status}\n"
                    f"{json.dumps(log_payload, indent=2, default=cls._default_serializer)}")

    @classmethod
    def log_planning(
        cls,
        claim_id: str,
        line_item_id: str,
        variant: str,
        benefit_bucket: str,
        steps: List[Dict[str, Any]],
        depth: int
    ) -> None:
        """Logs DAG execution planning compiled by the AI Planner."""
        logger = cls.get_logger()
        log_payload = {
            "event": "PLANNER_DAG_COMPILED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "claim_id": claim_id,
            "line_item_id": line_item_id,
            "variant": variant,
            "benefit_bucket": benefit_bucket,
            "dependency_depth": depth,
            "execution_steps": steps
        }
        logger.info(f"PLANNER | Claim: {claim_id} | Line Item: {line_item_id} | Steps: {len(steps)} | Depth: {depth}\n"
                    f"{json.dumps(log_payload, indent=2, default=cls._default_serializer)}")

    @classmethod
    def log_tool_call(
        cls,
        claim_id: str,
        line_item_id: Optional[str],
        tool_name: str,
        arguments: Dict[str, Any],
        output: Any
    ) -> None:
        """Logs a deterministic calculator/tool invocation with its arguments and results."""
        logger = cls.get_logger()
        
        # Serialize dataclasses or custom classes to dict if needed
        serialized_output = output
        if hasattr(output, "__dict__"):
            serialized_output = getattr(output, "__dict__")
        elif hasattr(output, "_asdict"):
            serialized_output = getattr(output, "_asdict")()

        log_payload = {
            "event": "TOOL_CALLED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "claim_id": claim_id,
            "line_item_id": line_item_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "output": serialized_output
        }
        logger.info(f"TOOL | Claim: {claim_id} | Tool: {tool_name}\n"
                    f"{json.dumps(log_payload, indent=2, default=cls._default_serializer)}")

    @classmethod
    def log_llm_call(
        cls,
        claim_id: str,
        line_item_id: Optional[str],
        rule_id: str,
        provider: str,
        url: Optional[str],
        system_prompt: str,
        user_prompt: str,
        raw_response: str,
        structured_output: Optional[Dict[str, Any]] = None,
        confidence: float = 0.0,
        requires_manual_review: bool = False
    ) -> None:
        """Logs a semantic reasoning LLM query, prompts, raw response, and confidence score."""
        logger = cls.get_logger()
        log_payload = {
            "event": "LLM_INFERENCE",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "claim_id": claim_id,
            "line_item_id": line_item_id,
            "rule_id": rule_id,
            "provider": provider,
            "url": url,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "raw_response": raw_response,
            "structured_output": structured_output,
            "confidence": confidence,
            "requires_manual_review": requires_manual_review
        }
        logger.info(f"LLM | Claim: {claim_id} | Rule: {rule_id} | Provider: {provider} | Confidence: {confidence:.2f}\n"
                    f"{json.dumps(log_payload, indent=2, default=cls._default_serializer)}")

    @classmethod
    def log_routing(
        cls,
        claim_id: str,
        line_item_id: Optional[str],
        gate: str,
        rule_id: str,
        confidence: float,
        action: str,
        reason: str
    ) -> None:
        """Logs confidence-based routing actions (Auto-Adjudicated, Assisted Review, or Pending Review)."""
        logger = cls.get_logger()
        log_payload = {
            "event": "CONFIDENCE_ROUTING",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "claim_id": claim_id,
            "line_item_id": line_item_id,
            "gate": gate,
            "rule_id": rule_id,
            "confidence": confidence,
            "action": action,
            "reason": reason
        }
        logger.info(f"ROUTING | Claim: {claim_id} | Gate: {gate} | Rule: {rule_id} | Action: {action} | Conf: {confidence:.2f}\n"
                    f"{json.dumps(log_payload, indent=2, default=cls._default_serializer)}")

    @classmethod
    def log_gate_evaluation(
        cls,
        claim_id: str,
        line_item_id: Optional[str],
        gate: str,
        rule_id: str,
        inputs: Dict[str, Any],
        evaluation_status: str,
        reason: str
    ) -> None:
        """Logs gate decision outputs and status evaluations."""
        logger = cls.get_logger()
        log_payload = {
            "event": "GATE_EVALUATION",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "claim_id": claim_id,
            "line_item_id": line_item_id,
            "gate": gate,
            "rule_id": rule_id,
            "inputs": inputs,
            "evaluation_status": evaluation_status,
            "reason": reason
        }
        logger.info(f"GATE | Claim: {claim_id} | Gate: {gate} | Rule: {rule_id} | Status: {evaluation_status}\n"
                    f"{json.dumps(log_payload, indent=2, default=cls._default_serializer)}")
