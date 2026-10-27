import pytest
from unittest.mock import MagicMock, patch
import socket
import json
from pydantic import BaseModel

from mock_llm import mock_llm_response

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
