import asyncio
from agents.mcp import MCPServerStreamableHttp
from app.conf.mcp_config import mcp_config


async def test_qcc():

    headers = {
        "Authorization": f"Bearer {mcp_config.qcc_api_key}"
    }

    print("发送Header:")
    print(headers)


    qcc_server = MCPServerStreamableHttp(
        params={
            "url": "https://agent.qcc.com/mcp/company/stream",
            "headers": headers
        }
    )


    try:
        await qcc_server.connect()

        print("连接成功")

        tools = await qcc_server.list_tools()

        print(tools)


    finally:
        await qcc_server.cleanup()


asyncio.run(test_qcc())
