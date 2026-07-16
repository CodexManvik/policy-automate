import pytest
from unittest.mock import MagicMock, patch
import socket
import json
from pydantic import BaseModel

# Mock response generator (copied from original _mock_llm_response)
def mock_llm_response(prompt: str, response_model: type[BaseModel]) -> str:
    prompt_lower = prompt.lower()
    
    if response_model.__name__ == "SemanticAdjudicationPayload":
        if "lifestyle assessment" in prompt_lower or "routine body optimization" in prompt_lower:
            return json.dumps({
                "evaluation_status": "PASSED",
                "reasoning_trace": "Claim details contain highly ambiguous clinical terms (lifestyle assessment, routine body optimization). Unable to determine medical necessity with certainty.",
                "confidence_score": 0.75
            })
            
        if "cosmetic" in prompt_lower or "plastic surgery" in prompt_lower or "rhinoplasty" in prompt_lower:
            if "accident" in prompt_lower or "burn" in prompt_lower or "cancer" in prompt_lower or "reconstruction" in prompt_lower:
                return json.dumps({
                    "evaluation_status": "PASSED",
                    "reasoning_trace": "Reconstruction after accident/burns/cancer is covered under policy section 5.1.7.",
                    "confidence_score": 0.92
                })
            else:
                return json.dumps({
                    "evaluation_status": "EXCLUSION_ACTIVE",
                    "reasoning_trace": "Treatment is cosmetic or plastic surgery which is strictly excluded under section 5.1.7.",
                    "confidence_score": 0.95
                })
        
        if ("maternity" in prompt_lower or "childbirth" in prompt_lower or "pregnancy" in prompt_lower 
            or "delivery" in prompt_lower or "caesarean" in prompt_lower or "c-section" in prompt_lower):
            if "ectopic" in prompt_lower:
                return json.dumps({
                    "evaluation_status": "PASSED",
                    "reasoning_trace": "Ectopic pregnancy is covered as an exception to maternity exclusion under section 5.1.16.",
                    "confidence_score": 0.98
                })
            else:
                return json.dumps({
                    "evaluation_status": "EXCLUSION_ACTIVE",
                    "reasoning_trace": "Maternity expenses (childbirth, pregnancy) are excluded except ectopic pregnancy under section 5.1.16.",
                    "confidence_score": 0.96
                })
        
        if "investigation" in prompt_lower or "diagnostic" in prompt_lower or "mri" in prompt_lower or "scan" in prompt_lower:
            if "treatment" in prompt_lower and "performed" in prompt_lower:
                return json.dumps({
                    "evaluation_status": "PASSED",
                    "reasoning_trace": "Diagnostics were part of active treatment protocol under section 5.1.4.",
                    "confidence_score": 0.90
                })
            else:
                return json.dumps({
                    "evaluation_status": "EXCLUSION_ACTIVE",
                    "reasoning_trace": "Admission primarily for diagnostic tests and evaluation without active treatment under section 5.1.4.",
                    "confidence_score": 0.88
                })
                
        if "appendicitis" in prompt_lower or "appendectomy" in prompt_lower:
            return json.dumps({
                "evaluation_status": "PASSED",
                "reasoning_trace": "Hospitalization for appendectomy is covered under inpatient hospitalization benefits.",
                "confidence_score": 0.93
            })
            
        # Default response
        return json.dumps({
            "evaluation_status": "PASSED",
            "reasoning_trace": "No active exclusion or waiting period satisfy criteria.",
            "confidence_score": 0.94
        })
        
    if response_model.__name__ == "ExclusionAssessment":
        if "cosmetic" in prompt_lower or "plastic surgery" in prompt_lower or "rhinoplasty" in prompt_lower:
            if "accident" in prompt_lower or "burn" in prompt_lower or "cancer" in prompt_lower or "reconstruction" in prompt_lower:
                return json.dumps({
                    "is_excluded": False,
                    "exclusion_type": None,
                    "confidence": 0.92,
                    "reason": "Reconstruction after accident/burns/cancer is covered",
                    "policy_section_ref": "5.1.7"
                })
            else:
                return json.dumps({
                    "is_excluded": True,
                    "exclusion_type": "Cosmetic Surgery",
                    "confidence": 0.95,
                    "reason": "Treatment appears cosmetic with no medical necessity",
                    "policy_section_ref": "5.1.7"
                })
        
        if ("maternity" in prompt_lower or "childbirth" in prompt_lower or "pregnancy" in prompt_lower 
            or "delivery" in prompt_lower or "caesarean" in prompt_lower or "c-section" in prompt_lower):
            if "ectopic" in prompt_lower:
                return json.dumps({
                    "is_excluded": False,
                    "exclusion_type": None,
                    "confidence": 0.98,
                    "reason": "Ectopic pregnancy is covered under policy",
                    "policy_section_ref": "5.1.16"
                })
            else:
                return json.dumps({
                    "is_excluded": True,
                    "exclusion_type": "Maternity",
                    "confidence": 0.96,
                    "reason": "Maternity expenses excluded except ectopic pregnancy",
                    "policy_section_ref": "5.1.16"
                })
        
        if "investigation" in prompt_lower or "diagnostic" in prompt_lower:
            if "treatment" in prompt_lower and "performed" in prompt_lower:
                return json.dumps({
                    "is_excluded": False,
                    "exclusion_type": None,
                    "confidence": 0.90,
                    "reason": "Diagnostics were part of active treatment",
                    "policy_section_ref": "5.1.4"
                })
            else:
                return json.dumps({
                    "is_excluded": True,
                    "exclusion_type": "Diagnostic Only",
                    "confidence": 0.88,
                    "reason": "Admission primarily for diagnostics only",
                    "policy_section_ref": "5.1.4"
                })
                
    # Default fallback
    return json.dumps({
        "evaluation_status": "PASSED",
        "reasoning_trace": "Passed by mock fallback",
        "confidence_score": 0.95
    })

# Autouse fixture to intercept and mock connection and LLM calls in test environment
@pytest.fixture(autouse=True)
def mock_external_calls():
    # 1. Mock socket.create_connection to always succeed for port checking
    original_create_connection = socket.create_connection
    def dummy_create_connection(address, timeout=None, source_address=None):
        host, port = address
        if host in ("127.0.0.1", "localhost") and port == 8080:
            mock_socket = MagicMock()
            return mock_socket
        return original_create_connection(address, timeout, source_address)

    # 2. Mock SemanticExecutionAgent._call_llm to return simulated responses
    from semantic_agent import SemanticExecutionAgent
    
    def dummy_call_llm(self, system_prompt: str, user_prompt: str, response_model: type[BaseModel]) -> str:
        return mock_llm_response(user_prompt, response_model)

    with patch("socket.create_connection", side_effect=dummy_create_connection), \
         patch.object(SemanticExecutionAgent, "_call_llm", autospec=True, side_effect=dummy_call_llm):
        yield
