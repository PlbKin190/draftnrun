import json
from unittest.mock import MagicMock, patch

import pytest
from openai.types.chat import ChatCompletionMessageToolCall

from engine.agent.react_function_calling import ReActAgent, INITIAL_PROMPT
from engine.agent.agent import AgentPayload, ToolDescription, ChatMessage
from engine.trace.trace_manager import TraceManager
from engine.llm_services.llm_service import LLMService


@pytest.fixture
def mock_agent():
    mock_agent = MagicMock(spec=ReActAgent)
    mock_tool_description = MagicMock(spec=ToolDescription)
    mock_tool_description.name = "test_tool"
    mock_tool_description.description = "Test tool description"
    mock_tool_description.tool_properties = {
        "test_property": {"type": "string", "description": "Test property description"}
    }
    mock_tool_description.required_tool_properties = ["test_property"]
    mock_agent.tool_description = mock_tool_description
    return mock_agent


@pytest.fixture
def mock_trace_manager():
    return MagicMock(spec=TraceManager)


@pytest.fixture
def mock_tool_description():
    tool_description = MagicMock(spec=ToolDescription)
    tool_description.name = "test_tool"
    tool_description.description = "Test tool description"
    tool_description.tool_properties = {
        "test_property": {"type": "string", "description": "Test property description"}
    }
    tool_description.required_tool_properties = ["test_property"]
    return tool_description


@pytest.fixture
def mock_llm_service():
    return MagicMock(spec=LLMService)


@pytest.fixture
def agent_input():
    return AgentPayload(messages=[ChatMessage(role="user", content="Test message")])


@pytest.fixture
def react_agent(mock_agent, mock_trace_manager, mock_tool_description, mock_llm_service):
    return ReActAgent(
        llm_service=mock_llm_service,
        component_instance_name="Test React Agent",
        agent_tools=[mock_agent],
        trace_manager=mock_trace_manager,
        tool_description=mock_tool_description,
    )


def test_run_no_tool_calls(react_agent, agent_input, mock_llm_service):
    mock_llm_service.function_call.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Test response", tool_calls=[]))]
    )

    output = react_agent.run_sync(agent_input)

    assert output.last_message.content == "Test response"
    assert output.is_final


def test_run_with_tool_calls(react_agent, agent_input, mock_agent, mock_llm_service):
    mock_tool_call = MagicMock()
    mock_tool_call.id = "1"
    mock_tool_call_function = MagicMock()
    mock_tool_call_function.name = "test_tool"
    mock_tool_call_function.arguments = json.dumps({"test_property": "Test value"})
    mock_tool_call.function = mock_tool_call_function
    mock_response_message = ChatMessage(role="assistant", content="Tool response")

    mock_llm_service.function_call.return_value = MagicMock(
        choices=[MagicMock(message=mock_response_message, tool_calls=[mock_tool_call])]
    )
    mock_agent.run.return_value = AgentPayload(
        messages=[ChatMessage(role="assistant", content="Tool response")], is_final=True
    )

    output = react_agent.run_sync(agent_input)

    assert output.last_message.role == "assistant"
    assert output.last_message.content == "Tool response"
    assert output.is_final


@patch.object(LLMService, "function_call")
def test_initial_prompt_insertion(mock_function_call, react_agent, agent_input):
    mock_function_call.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Test response", tool_calls=[]))]
    )
    react_agent.run_sync(agent_input)
    assert agent_input.messages[0].role == "system"
    assert agent_input.messages[0].content == INITIAL_PROMPT


@patch.object(LLMService, "function_call")
def test_max_iterations(mock_function_call, react_agent, agent_input, mock_agent):
    mock_tool_call = MagicMock(spec=ChatCompletionMessageToolCall)
    mock_tool_call.id = "1"
    mock_tool_call_function = MagicMock()
    mock_tool_call_function.name = "test_tool"
    mock_tool_call_function.arguments = json.dumps({"test_property": "Test value"})
    mock_tool_call.function = mock_tool_call_function
    mock_function_call.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Test response", tool_calls=[mock_tool_call]))]
    )

    react_agent._max_iterations = 1
    mock_agent.run.return_value = AgentPayload(
        messages=[ChatMessage(role="assistant", content="Tool response")], is_final=False
    )

    output = react_agent.run_sync(agent_input)

    assert output.last_message.content == "I'm sorry, I couldn't find a solution to your problem."
    assert not output.is_final


def test_react_agent_without_tools(mock_trace_manager, mock_tool_description, mock_llm_service):
    """Test that ReActAgent can be instantiated without tools."""
    react_agent = ReActAgent(
        llm_service=mock_llm_service,
        component_instance_name="Test React Agent Without Tools",
        trace_manager=mock_trace_manager,
        tool_description=mock_tool_description,
    )

    assert react_agent.agent_tools == []
    assert react_agent.component_instance_name == "Test React Agent Without Tools"
    assert react_agent._max_iterations == 3
    assert react_agent.initial_prompt == INITIAL_PROMPT
