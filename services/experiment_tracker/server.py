"""experiment_tracker MCP 서버 — stdio transport.

실행: python -m experiment_tracker.server
"""
import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .store import compare_runs, create_experiment, init_schema, log_run

app = Server("experiment_tracker")


@app.list_tools()
async def list_tools() -> list[Tool]:
    init_schema()
    return [
        Tool(
            name="create_experiment",
            description="새 실험 생성. 전략 사양과 연결.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name":        {"type": "string"},
                    "strategy_id": {"type": "string", "description": "workspace/specs/ 파일명"},
                    "description": {"type": "string", "default": ""},
                },
                "required": ["name", "strategy_id"],
            },
        ),
        Tool(
            name="log_run",
            description="백테스트 실행 결과 기록.",
            inputSchema={
                "type": "object",
                "properties": {
                    "experiment_id":  {"type": "string"},
                    "run_id":         {"type": "string", "description": "backtest engine이 생성한 run_id"},
                    "params":         {"type": "object"},
                    "metrics":        {"type": "object"},
                    "artifact_paths": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["experiment_id", "run_id", "params", "metrics", "artifact_paths"],
            },
        ),
        Tool(
            name="compare_runs",
            description="실험 내 여러 run을 메트릭 기준으로 비교.",
            inputSchema={
                "type": "object",
                "properties": {
                    "experiment_id": {"type": "string"},
                    "run_ids":       {"type": "array", "items": {"type": "string"}, "default": []},
                    "sort_by":       {"type": "string", "default": "sharpe"},
                },
                "required": ["experiment_id"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "create_experiment":
        result = create_experiment(
            name=arguments["name"],
            strategy_id=arguments["strategy_id"],
            description=arguments.get("description", ""),
        )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    if name == "log_run":
        result = log_run(
            experiment_id=arguments["experiment_id"],
            run_id=arguments["run_id"],
            params=arguments["params"],
            metrics=arguments["metrics"],
            artifact_paths=arguments["artifact_paths"],
        )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    if name == "compare_runs":
        result = compare_runs(
            experiment_id=arguments["experiment_id"],
            run_ids=arguments.get("run_ids", []),
            sort_by=arguments.get("sort_by", "sharpe"),
        )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    raise ValueError(f"Unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
