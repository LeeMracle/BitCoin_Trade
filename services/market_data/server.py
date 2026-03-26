"""market_data MCP 서버 — stdio transport.

실행: python -m market_data.server
"""
import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .fetcher import fetch_fear_greed, fetch_ohlcv

app = Server("market_data")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_ohlcv",
            description="업비트 BTC/KRW OHLCV 캔들 조회. DuckDB 캐시 사용.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol":    {"type": "string", "default": "BTC/KRW"},
                    "timeframe": {"type": "string", "enum": ["1m","3m","5m","15m","30m","1h","4h","1d","1w"]},
                    "start":     {"type": "string", "description": "ISO8601 UTC e.g. 2024-01-01T00:00:00Z"},
                    "end":       {"type": "string", "description": "ISO8601 UTC"},
                    "use_cache": {"type": "boolean", "default": True},
                },
                "required": ["timeframe", "start", "end"],
            },
        ),
        Tool(
            name="get_ticker",
            description="업비트 현재가 및 24h 거래량 조회.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "default": "BTC/KRW"},
                },
            },
        ),
        Tool(
            name="get_orderbook",
            description="업비트 호가창 조회.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "default": "BTC/KRW"},
                    "limit":  {"type": "integer", "default": 10},
                },
            },
        ),
        Tool(
            name="get_macro_series",
            description="매크로 시계열 조회. series_id: FEAR_GREED",
            inputSchema={
                "type": "object",
                "properties": {
                    "series_id": {"type": "string", "enum": ["FEAR_GREED"]},
                    "start":     {"type": "string"},
                    "end":       {"type": "string"},
                },
                "required": ["series_id", "start", "end"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "get_ohlcv":
        rows = await fetch_ohlcv(
            symbol=arguments.get("symbol", "BTC/KRW"),
            timeframe=arguments["timeframe"],
            start=arguments["start"],
            end=arguments["end"],
            use_cache=arguments.get("use_cache", True),
        )
        return [TextContent(type="text", text=json.dumps(rows, ensure_ascii=False))]

    if name == "get_ticker":
        import ccxt.async_support as ccxt_async
        exchange = ccxt_async.upbit()
        try:
            ticker = await exchange.fetch_ticker(arguments.get("symbol", "BTC/KRW"))
        finally:
            await exchange.close()
        result = {
            "ts": ticker["timestamp"],
            "last": ticker["last"],
            "bid": ticker["bid"],
            "ask": ticker["ask"],
            "volume_24h": ticker["quoteVolume"],
            "change_pct_24h": ticker["percentage"],
        }
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    if name == "get_orderbook":
        import ccxt.async_support as ccxt_async
        exchange = ccxt_async.upbit()
        try:
            ob = await exchange.fetch_order_book(
                arguments.get("symbol", "BTC/KRW"),
                limit=arguments.get("limit", 10),
            )
        finally:
            await exchange.close()
        result = {
            "ts": ob["timestamp"],
            "bids": ob["bids"],
            "asks": ob["asks"],
        }
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    if name == "get_macro_series":
        series_id = arguments["series_id"]
        if series_id == "FEAR_GREED":
            rows = await fetch_fear_greed(arguments["start"], arguments["end"])
            return [TextContent(type="text", text=json.dumps(rows, ensure_ascii=False))]
        raise ValueError(f"Unknown series_id: {series_id}")

    raise ValueError(f"Unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
