"""Provider-neutral native tool loop with discovery and verified completion."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent_tools import (
    DISCOVERY_INSTRUCTION,
    FINISH_RESPONSE_TOOL,
    MAX_EVIDENCE_CALLS,
    compact_tool_catalog,
    discover_tool_schemas,
    make_discovery_tool,
)

logger = logging.getLogger(__name__)
MAX_ROUNDS = 16
MAX_ACTIONS = 24
MAX_LOADED_TOOLS = 32


def _native_calls(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = response.get("tool_calls")
    if not raw and isinstance(response.get("function_call"), dict):
        raw = [{"function": response["function_call"]}]
    calls = []
    for index, call in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
            continue
        function = call["function"]
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        arguments = function.get("arguments", "{}")
        normalized = {
            "id": str(call.get("id") or f"native-{index}"),
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False) if isinstance(arguments, dict) else str(arguments),
            },
        }
        if isinstance(call.get("extra_content"), dict):
            normalized["extra_content"] = copy.deepcopy(call["extra_content"])
        calls.append(normalized)
    return calls


def _finish_error(args: Any, receipts: list[dict[str, Any]]) -> str | None:
    if not isinstance(args, dict) or set(args) != {"text", "outcome", "evidence_call_ids"}:
        return "Нужны text, outcome, evidence_call_ids."
    if not isinstance(args["text"], str) or not args["text"].strip() or len(args["text"]) > 6000:
        return "text должен содержать от 1 до 6000 символов."
    outcome, evidence = args["outcome"], args["evidence_call_ids"]
    if not isinstance(outcome, str) or outcome not in {"answered", "completed", "partial", "blocked"}:
        return "Некорректный outcome."
    known = {item["call_id"] for item in receipts}
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE_CALLS or any(not isinstance(i, str) or i not in known for i in evidence):
        return "evidence_call_ids должен ссылаться только на полученные результаты операций."
    writes = [item for item in receipts if item["mutating"]]
    succeeded = [item for item in writes if item["status"] == "success"]
    if outcome == "completed" and (not succeeded or any(item["status"] != "success" for item in writes)):
        return "Выполнение не подтверждено: нужны успешные действия; ошибки и ожидающие подтверждения не являются успехом."
    if outcome == "partial" and not succeeded:
        return "Нет успешных изменений для partial; используй blocked или answered по фактическому результату."
    if outcome == "answered" and writes:
        return "Были попытки изменения состояния: укажи completed, partial или blocked и их фактический исход."
    if outcome == "blocked" and succeeded:
        return "Часть действий уже выполнена; сообщи об этом с outcome=partial."
    if not {item["call_id"] for item in writes} <= set(evidence):
        return "Укажи evidence_call_ids всех попыток изменения состояния, включая ошибки и подтверждения."
    return None


def _incomplete(receipts: list[dict[str, Any]], reason: str) -> str:
    writes = [str(item["result"])[:400] for item in receipts if item["mutating"]]
    if writes:
        return (reason + "\nФактические результаты действий:\n" + "\n".join(dict.fromkeys(writes)))[:12000]
    return reason + " Серверные изменения не выполнялись."


async def run_agent_turn(
    messages: list[dict], *,
    schemas: Mapping[str, dict], eligible_names: frozenset[str], mutating_names: frozenset[str],
    ask_user_schema: dict,
    complete: Callable[..., Awaitable[dict | None]],
    execute: Callable[[dict], Awaitable[dict[str, str]]],
    is_current: Callable[[], bool],
    prepare_text: Callable[[str], str],
    has_internal_syntax: Callable[[str], bool],
    state: dict | None = None,
    completion_options: dict | None = None,
    timeout: float = 240.0,
) -> str:
    """The model chooses chat, discovery, actions, recovery and final response.

    An authenticated capability list limits discovery, not task planning.
    External observations never alter that list. All writes still go through
    the caller's verified executor, and are marked before awaiting any effect.
    """
    conversation = copy.deepcopy(messages)
    conversation.append({"role": "system", "content": DISCOVERY_INSTRUCTION + "\n" + compact_tool_catalog(schemas, eligible_names, mutating_names)})
    loaded: dict[str, dict] = {}
    base_tools = [make_discovery_tool(eligible_names), ask_user_schema, FINISH_RESPONSE_TOOL]
    receipts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    writes_seen: dict[str, dict[str, str]] = {}
    deadline = asyncio.get_running_loop().time() + timeout
    attempts = 0
    malformed = 0

    def remaining() -> float:
        return max(0.0, deadline - asyncio.get_running_loop().time())

    for round_index in range(MAX_ROUNDS):
        if not is_current():
            return _incomplete(receipts, "Запрос изменился; продолжение остановлено.")
        if remaining() <= 0:
            break
        options = dict(completion_options or {})
        available_this_step = frozenset(loaded)
        options["timeout"] = max(1, int(min(float(options.get("timeout", 60)), remaining())))
        try:
            response = await asyncio.wait_for(complete(
                copy.deepcopy(conversation), tools=copy.deepcopy(base_tools + list(loaded.values())),
                tool_choice="required", **options,
            ), timeout=remaining())
        except Exception as exc:
            logger.warning("Agent completion failed at round %s (%s)", round_index, type(exc).__name__)
            return _incomplete(receipts, "AI не смог завершить ответ.")
        if not is_current():
            return _incomplete(receipts, "Запрос изменился; продолжение остановлено.")
        calls = _native_calls(response or {})
        if not calls:
            malformed += 1
            if malformed > 1:
                return _incomplete(receipts, "AI не вернул корректный ответ через интерфейс инструментов.")
            conversation.append({"role": "system", "content": "Используй native tool_calls. Для обычного ответа вызови finish_response; код и обещания в content ничего не выполняют."})
            continue
        # Do not execute a truncated batch with missing protocol responses.
        if len(calls) > MAX_ACTIONS:
            return _incomplete(receipts, "Слишком много операций в одном шаге; задача остановлена.")
        for index, call in enumerate(calls):
            if call["id"] in seen_ids:
                call["id"] = f"pos-agent-{round_index}-{index}"
                while call["id"] in seen_ids:
                    call["id"] += "-duplicate"
            seen_ids.add(call["id"])
        # Intermediate prose cannot masquerade as successful work in Discord.
        conversation.append({"role": "assistant", "content": None, "tool_calls": calls})
        for call in calls:
            if not is_current():
                return _incomplete(receipts, "Запрос изменился; продолжение остановлено.")
            name, raw = call["function"]["name"], call["function"]["arguments"]
            try:
                args = json.loads(raw)
            except (TypeError, ValueError):
                args = None
            observation: dict[str, Any]
            recorded = False
            if name == "discover_tools":
                selected, error = discover_tool_schemas(args, schemas, eligible_names)
                new_names = {item["function"]["name"] for item in selected}
                if not error and len(set(loaded) | new_names) > MAX_LOADED_TOOLS:
                    error = "Достигнут размер набора инструментов в этой задаче. Заверши текущую часть перед продолжением."
                if error:
                    observation = {"status": "error", "result": error}
                else:
                    loaded.update({item["function"]["name"]: item for item in selected})
                    observation = {"status": "success", "available_tools": sorted(loaded), "result": "Инструменты доступны со следующего модельного шага; это только открытие возможностей, никакие действия не выполнены."}
            elif name == "finish_response":
                error = _finish_error(args, receipts)
                if len(calls) != 1:
                    error = "finish_response должен быть отдельным шагом после получения результатов всех операций."
                if error is None and has_internal_syntax(args["text"]):
                    error = "Убери служебные вызовы из текста ответа."
                if error is None:
                    return prepare_text(args["text"].strip())
                observation = {"status": "error", "result": error}
            elif name == "ask_user":
                question = args.get("question") if isinstance(args, dict) else None
                if len(calls) == 1 and isinstance(question, str) and question.strip() and len(question) <= 1500:
                    text = prepare_text(question.strip())
                    return _incomplete(receipts, text) if any(item["mutating"] for item in receipts) else text
                observation = {"status": "error", "result": "ask_user требует один отдельный вызов с непустым question до 1500 символов."}
            elif name not in eligible_names or name not in available_this_step:
                observation = {"status": "error", "result": "Инструмент не открыт или недоступен. Сначала вызови discover_tools для нужной возможности."}
            elif not isinstance(args, dict):
                observation = {"status": "error", "result": "Аргументы должны быть корректным JSON-объектом. Операция не выполнялась."}
            elif attempts >= MAX_ACTIONS or remaining() <= 0:
                observation = {"status": "error", "result": "Лимит операций исчерпан. Заверши задачу по имеющимся результатам; оставшееся не выполнено."}
            else:
                attempts += 1
                key = name + ":" + json.dumps(args, sort_keys=True, ensure_ascii=False)
                duplicate = name in mutating_names and key in writes_seen
                if duplicate:
                    outcome = writes_seen[key]
                elif name == "undo_recent_actions" and any(item["mutating"] for item in receipts):
                    outcome = {"status": "error", "result": "Откат предыдущего поручения должен выполняться до новых изменений. После изменений требуется отдельный запрос на откат."}
                else:
                    # Snapshot before execute: timeouts never replay effects.
                    if state is not None:
                        state["tools_executed"] = True
                    if name in mutating_names:
                        writes_seen[key] = {"status": "unknown", "result": "Исход попытки неизвестен; автоматический повтор запрещён."}
                    try:
                        outcome = await asyncio.wait_for(execute(call), timeout=remaining())
                    except Exception as exc:
                        logger.warning("Agent tool failed: %s (%s)", name, type(exc).__name__)
                        outcome = {"status": "unknown", "result": "Не удалось подтвердить результат операции. Не повторяй её без проверки состояния."}
                    if name in mutating_names:
                        writes_seen[key] = outcome
                receipt = {"call_id": call["id"], "name": name, "mutating": name in mutating_names,
                           "status": outcome["status"], "result": outcome["result"]}
                receipts.append(receipt)
                recorded = True
                observation = {**receipt, "duplicate": duplicate}
                if state is not None:
                    state["tool_results"] = copy.deepcopy(receipts)
                logger.info("P.OS agent tool: name=%s status=%s duplicate=%s", name, outcome["status"], duplicate)
            if not recorded and name in schemas:
                # Rejected operations still matter to the task's final status.
                # A successful earlier write cannot hide a later refused one.
                receipt = {"call_id": call["id"], "name": name,
                           "mutating": name in mutating_names,
                           "status": observation["status"], "result": observation["result"]}
                receipts.append(receipt)
                observation = {**observation, **receipt}
                if state is not None:
                    state["tool_results"] = copy.deepcopy(receipts)
            if isinstance(observation.get("result"), str):
                observation["result"] = observation["result"][:12000]
            conversation.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(observation, ensure_ascii=False)})
    return _incomplete(receipts, "Достигнут предел времени или шагов задачи.")
