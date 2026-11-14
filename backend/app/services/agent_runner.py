"""Agent runner service.

Executes one run synchronously inside a Celery worker:

1. loads the agent configuration and per-run overrides;
2. runs an LLM/tool-calling loop;
3. records every LLM call, tool call and data access as a trace span;
4. accumulates token usage and cost per step;
5. publishes live progress events to Redis for SSE/WebSocket clients.

The runner never talks to HTTP directly: provider adapters and the tool registry
are injected, which keeps the loop testable and provider agnostic.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents.tools import (
    ResolvedTool,
    ToolConfigurationError,
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolResult,
    tool_registry,
)
from app.core import telemetry
from app.core.config import settings
from app.core.logging import run_id_var
from app.core.redis import get_publisher, is_cancelled
from app.core.serialization import to_jsonable, truncate_json
from app.models import Agent, Run, RunStatus, SpanType, utcnow
from app.services.cost_calculator import compute_cost, quantize_cost
from app.services.llm import (
    LLMError,
    LLMProvider,
    LLMResponse,
    ResolvedProvider,
    ToolSpec,
    assistant_message,
    resolve_provider,
    tool_message,
)
from app.services.trace_collector import EventPublisherProtocol, TraceCollector

logger = logging.getLogger(__name__)

CANCELLED_MESSAGE = "Cancelled by user"
MAX_ERROR_LENGTH = 8_000


class AgentRunCancelled(Exception):
    """Raised when an operator cancels a run."""


class AgentTimeoutError(Exception):
    """Raised when a run exceeds its wall-clock budget."""


class AgentExecutionError(Exception):
    """Raised for unrecoverable execution problems."""


class ProviderFactory(Protocol):
    def __call__(self, model: str) -> ResolvedProvider: ...


@dataclass
class RunExecutionResult:
    run_id: UUID
    status: RunStatus
    output: str | None
    error: str | None
    total_tokens: int
    total_cost_usd: Decimal
    latency_ms: int


class AgentRunner:
    def __init__(
        self,
        db: Session,
        run: Run,
        agent: Agent,
        *,
        publisher: EventPublisherProtocol | None = None,
        registry: ToolRegistry | None = None,
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        self.db = db
        self.run = run
        self.agent = agent
        self.registry = registry or tool_registry
        self.provider_factory: ProviderFactory = provider_factory or resolve_provider
        self.collector = TraceCollector(
            db,
            run,
            publisher=publisher or get_publisher(),
            traceparent=self._traceparent(),
        )

        execution = self._execution_options()
        self.model = str(execution.get("model_name") or agent.model_name)
        self.temperature = float(execution.get("temperature", settings.agent_temperature))
        self.max_iterations = int(execution.get("max_iterations", settings.agent_max_iterations))
        self.max_tokens = int(execution.get("max_tokens", settings.agent_max_tokens))
        self.timeout_seconds = int(
            execution.get("timeout_seconds", settings.agent_default_timeout_seconds)
        )

        # A replay can override the agent's prompt and tool set without touching
        # the agent row; the values live in ``metadata.replay.overrides``.
        agent_overrides = self._replay_agent_overrides()
        system_prompt = agent_overrides.get("system_prompt")
        self.system_prompt = system_prompt if isinstance(system_prompt, str) else agent.system_prompt
        tools = agent_overrides.get("tools")
        self.tools_config = (
            [{"name": name, "config": {}} for name in tools if isinstance(name, str)]
            if isinstance(tools, list)
            else agent.tools_config
        )

        self._started_perf = time.perf_counter()
        self._total_input = 0
        self._total_output = 0
        self._total_cost = Decimal("0")
        self._active_incremented = False
        self._tools: dict[str, ResolvedTool] = {}
        self._tool_specs: list[ToolSpec] = []

    # -- public API ----------------------------------------------------
    def execute(self) -> RunExecutionResult:
        """Run the agent and persist the terminal state.

        Expected failures are recorded on the run instead of being re-raised, so
        Celery does not retry a deterministic failure (bad prompt, missing key).
        """
        token = run_id_var.set(str(self.run.id))
        try:
            self._mark_running()
            try:
                with self.collector.run_span(
                    "agent.run",
                    input_data={
                        "input_prompt": self.run.input_prompt,
                        "model": self.model,
                        "agent": self.agent.name,
                    },
                ) as root:
                    try:
                        output = self._execute_loop()
                    finally:
                        # The root span is flushed on exit, so populate totals first.
                        root.tokens_input = self._total_input
                        root.tokens_output = self._total_output
                        root.cost_override = quantize_cost(self._total_cost)
                        root.model_name = self.model
                    root.output_data = {"output_response": output}
                return self._complete(output)
            except AgentRunCancelled:
                return self._fail(CANCELLED_MESSAGE, cancelled=True)
            except (AgentTimeoutError, LLMError, ToolConfigurationError, AgentExecutionError) as exc:
                return self._fail(f"{type(exc).__name__}: {exc}")
            except Exception as exc:  # noqa: BLE001 - recorded and surfaced on the run
                logger.exception("Unexpected agent failure for run %s", self.run.id)
                return self._fail(f"{type(exc).__name__}: {exc}")
        finally:
            run_id_var.reset(token)
            telemetry.force_flush()

    # -- lifecycle -----------------------------------------------------
    def _mark_running(self) -> None:
        self.run.status = RunStatus.RUNNING
        self.run.started_at = utcnow()
        self.run.completed_at = None
        self.run.error_message = None
        self.db.commit()
        self._active_incremented = True
        telemetry.active_runs.add(1)
        self.collector.publish(
            "run.started",
            {
                "run_id": str(self.run.id),
                "agent_id": str(self.agent.id),
                "agent_name": self.agent.name,
                "model": self.model,
                "status": RunStatus.RUNNING.value,
            },
        )
        logger.info("Run started (agent=%s model=%s)", self.agent.name, self.model)

    def _complete(self, output: str | None) -> RunExecutionResult:
        latency_ms = self._elapsed_ms()
        self.run.status = RunStatus.COMPLETED
        self.run.output_response = output
        self.run.completed_at = utcnow()
        self.run.latency_ms = latency_ms
        self.run.total_tokens_used = self._total_input + self._total_output
        self.run.total_cost_usd = quantize_cost(self._total_cost)
        self.db.commit()
        self._release_active()

        telemetry.runs_counter.add(1, {"status": RunStatus.COMPLETED.value})
        self.collector.publish(
            "run.completed",
            {
                "run_id": str(self.run.id),
                "status": RunStatus.COMPLETED.value,
                "output_preview": (output or "")[:1_000],
                "total_tokens_used": self.run.total_tokens_used,
                "total_cost_usd": float(self.run.total_cost_usd),
                "latency_ms": latency_ms,
            },
        )
        logger.info(
            "Run completed (tokens=%s cost=%s latency=%sms)",
            self.run.total_tokens_used,
            self.run.total_cost_usd,
            latency_ms,
        )
        return RunExecutionResult(
            run_id=self.run.id,
            status=RunStatus.COMPLETED,
            output=output,
            error=None,
            total_tokens=self.run.total_tokens_used,
            total_cost_usd=self.run.total_cost_usd,
            latency_ms=latency_ms,
        )

    def _fail(self, message: str, *, cancelled: bool = False) -> RunExecutionResult:
        safe_message = message[:MAX_ERROR_LENGTH]
        latency_ms = self._elapsed_ms()
        try:
            self.run.status = RunStatus.FAILED
            self.run.error_message = safe_message
            self.run.completed_at = utcnow()
            self.run.latency_ms = latency_ms
            self.run.total_tokens_used = self._total_input + self._total_output
            self.run.total_cost_usd = quantize_cost(self._total_cost)
            self.db.commit()
        except Exception:  # pragma: no cover - last resort during failure handling
            logger.exception("Could not persist failure state for run %s", self.run.id)
            self.db.rollback()
        finally:
            self._release_active()

        telemetry.runs_counter.add(1, {"status": RunStatus.FAILED.value})
        self.collector.publish(
            "run.failed",
            {
                "run_id": str(self.run.id),
                "status": RunStatus.FAILED.value,
                "error_message": safe_message,
                "cancelled": cancelled,
                "latency_ms": latency_ms,
            },
        )
        logger.warning("Run failed: %s", safe_message)
        return RunExecutionResult(
            run_id=self.run.id,
            status=RunStatus.FAILED,
            output=None,
            error=safe_message,
            total_tokens=self.run.total_tokens_used,
            total_cost_usd=self.run.total_cost_usd,
            latency_ms=latency_ms,
        )

    def _release_active(self) -> None:
        if self._active_incremented:
            telemetry.active_runs.add(-1)
            self._active_incremented = False

    # -- execution loop ------------------------------------------------
    def _execute_loop(self) -> str | None:
        resolved = self.provider_factory(self.model)
        provider: LLMProvider = resolved.provider
        # Pricing and tracing use the canonical, prefix-free model id.
        self.model = resolved.model

        self._tools = self.registry.resolve(self.tools_config)
        self._tool_specs = [
            ToolSpec(
                name=resolved_tool.tool.spec.name,
                description=resolved_tool.tool.spec.description,
                parameters=resolved_tool.tool.spec.parameters,
            )
            for resolved_tool in self._tools.values()
        ]

        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": self.run.input_prompt})

        for iteration in range(1, self.max_iterations + 1):
            self._check_deadline()
            self._check_cancelled()

            with self.collector.span(
                f"agent.iteration.{iteration}",
                SpanType.DECISION,
                input_data={"iteration": iteration, "message_count": len(messages)},
                attributes={"acr.iteration": iteration},
            ) as decision:
                response = self._call_llm(provider, messages)
                messages.append(assistant_message(response))

                if not response.tool_calls:
                    decision.output_data = {
                        "action": "final_answer",
                        "iteration": iteration,
                        "finish_reason": response.finish_reason,
                    }
                    return response.content

                tool_calls_summary: list[dict[str, Any]] = []
                for call in response.tool_calls:
                    self._check_deadline()
                    self._check_cancelled()
                    message = self._execute_tool_call(call.id, call.name, call.arguments)
                    messages.append(message)
                    tool_calls_summary.append(
                        {"name": call.name, "arguments": call.arguments, "success": not message["is_error"]}
                    )

                decision.output_data = {
                    "action": "tools",
                    "iteration": iteration,
                    "tool_calls": tool_calls_summary,
                }

                self.collector.publish(
                    "run.progress",
                    {"iteration": iteration, "message_count": len(messages)},
                )

        raise AgentExecutionError(
            f"maximum iterations ({self.max_iterations}) reached without a final answer"
        )

    def _call_llm(self, provider: LLMProvider, messages: list[dict[str, Any]]) -> LLMResponse:
        with self.collector.span(
            f"llm.{self.model}",
            SpanType.LLM_CALL,
            input_data={
                "model": self.model,
                "messages": truncate_json(messages, max_string=8_000, max_items=100),
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            },
            model_name=self.model,
            attributes={"gen_ai.system": provider.name},
        ) as span:
            try:
                response = provider.complete(
                    model=self.model,
                    messages=messages,
                    tools=self._tool_specs or None,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
            except LLMError as exc:
                span.set_error(
                    str(exc),
                    details={
                        "provider": provider.name,
                        "retryable": getattr(exc, "retryable", False),
                        "type": type(exc).__name__,
                    },
                )
                raise

            cost = compute_cost(self.model, response.tokens_input, response.tokens_output)
            span.tokens_input = response.tokens_input
            span.tokens_output = response.tokens_output
            span.cost_override = cost
            span.model_name = response.model or self.model
            span.set_attribute("gen_ai.usage.input_tokens", response.tokens_input)
            span.set_attribute("gen_ai.usage.output_tokens", response.tokens_output)
            span.set_attribute("gen_ai.response.finish_reason", response.finish_reason or "unknown")
            span.set_attribute("acr.cost_usd", float(cost))
            span.set_attribute("acr.attempts", response.attempts)
            span.output_data = {
                "content": response.content,
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in response.tool_calls
                ],
                "finish_reason": response.finish_reason,
                "attempts": response.attempts,
            }

            self._record_usage(response.tokens_input, response.tokens_output, cost)
            return response

    def _execute_tool_call(
        self, tool_call_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        resolved_tool = self._tools.get(tool_name)
        with self.collector.span(
            f"tool.{tool_name}",
            SpanType.TOOL_CALL,
            input_data={"tool_call_id": tool_call_id, "arguments": arguments},
            attributes={"gen_ai.tool.name": tool_name},
        ) as span:
            draft = span.add_tool_call(tool_name, {"arguments": arguments})

            if resolved_tool is None:  # defensive: config resolution already validates
                message = f"Unknown tool '{tool_name}'"
                span.set_error(message)
                draft.fail(message)
                return tool_message(tool_call_id, tool_name, message, is_error=True)

            try:
                result = self._run_tool(resolved_tool, arguments)
            except ToolError as exc:
                span.set_error(str(exc), details={"tool": tool_name})
                draft.fail(str(exc))
                return tool_message(tool_call_id, tool_name, f"Tool error: {exc}", is_error=True)
            except Exception as exc:  # noqa: BLE001 - tool crash is recoverable by the model
                logger.exception("Tool %s raised unexpectedly", tool_name)
                message = f"Tool crashed: {type(exc).__name__}: {exc}"
                span.set_error(message, details={"tool": tool_name})
                draft.fail(message)
                return tool_message(tool_call_id, tool_name, message, is_error=True)

            draft.succeed(result.output)
            span.output_data = {
                "tool_call_id": tool_call_id,
                "output": result.output,
                "data_touched": result.data_touched,
            }

            # Record each external data source as its own span for auditability.
            for source in result.data_touched:
                with self.collector.span(
                    f"data_access.{source.get('type', 'unknown')}",
                    SpanType.DATA_ACCESS,
                    input_data={"source": source},
                ) as data_span:
                    data_span.output_data = source

            from app.agents.tools import to_json_argument

            return tool_message(tool_call_id, tool_name, to_json_argument(result.output))

    def _run_tool(self, resolved_tool: ResolvedTool, arguments: dict[str, Any]) -> ToolResult:
        """Run a tool with a hard timeout.

        The worker thread cannot be killed, so the timeout returns control to the
        agent while the in-flight call is left to finish on its own. Built-in
        tools all have their own network timeouts, so this is a last resort.
        """
        if not isinstance(arguments, dict):
            raise ToolError("tool arguments must be a JSON object")

        context = ToolContext(
            run_id=self.run.id,
            agent_id=self.agent.id,
            config=resolved_tool.config,
        )
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{resolved_tool.tool.spec.name}")
        future = executor.submit(resolved_tool.tool.run, arguments, context)
        try:
            return future.result(timeout=settings.tool_timeout_seconds)
        except FuturesTimeoutError as exc:
            future.cancel()
            raise ToolError(
                f"tool timed out after {settings.tool_timeout_seconds:.0f}s"
            ) from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    # -- accounting ----------------------------------------------------
    def _record_usage(self, tokens_input: int, tokens_output: int, cost: Decimal) -> None:
        self._total_input += max(int(tokens_input), 0)
        self._total_output += max(int(tokens_output), 0)
        self._total_cost = quantize_cost(self._total_cost + cost)

        # Commit after every step so the dashboard shows cost while the run is live.
        self.run.total_tokens_used = self._total_input + self._total_output
        self.run.total_cost_usd = self._total_cost
        try:
            self.db.commit()
        except Exception:  # pragma: no cover - accounting will be retried on completion
            logger.exception("Could not persist incremental usage for run %s", self.run.id)
            self.db.rollback()

        self.collector.publish(
            "run.usage",
            {
                "total_tokens_used": self._total_input + self._total_output,
                "total_cost_usd": float(self._total_cost),
            },
        )

    # -- guards --------------------------------------------------------
    def _elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._started_perf) * 1000)

    def _check_deadline(self) -> None:
        if self._elapsed_ms() >= self.timeout_seconds * 1000:
            raise AgentTimeoutError(f"run exceeded its {self.timeout_seconds}s deadline")

    def _check_cancelled(self) -> None:
        if is_cancelled(self.run.id):
            raise AgentRunCancelled(CANCELLED_MESSAGE)
        # Allow cancellation when Redis is unavailable but the row was updated.
        self.db.refresh(self.run, ["status", "error_message"])
        if self.run.status == RunStatus.FAILED and (
            self.run.error_message or ""
        ).startswith(CANCELLED_MESSAGE):
            raise AgentRunCancelled(CANCELLED_MESSAGE)

    def _execution_options(self) -> dict[str, Any]:
        raw = (self.run.metadata_json or {}).get("execution")
        return raw if isinstance(raw, dict) else {}

    def _replay_agent_overrides(self) -> dict[str, Any]:
        replay = (self.run.metadata_json or {}).get("replay")
        if not isinstance(replay, dict):
            return {}
        overrides = replay.get("overrides")
        return overrides if isinstance(overrides, dict) else {}

    def _traceparent(self) -> str | None:
        value = (self.run.metadata_json or {}).get("traceparent")
        return value if isinstance(value, str) else None
