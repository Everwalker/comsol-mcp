from __future__ import annotations

import unittest

import jsonschema
from mcp.server import Server
from mcp.shared.memory import create_connected_server_and_client_session

from comsol_mcp.mcp_server import build_mcp_protocol_server
from test_registration import EXPECTED_TOOLS


class ActualPublicRegistration(unittest.IsolatedAsyncioTestCase):
    async def test_real_public_registry_schema_over_memory_session(self):
        server = build_mcp_protocol_server()
        self.assertIsInstance(server, Server)
        async with create_connected_server_and_client_session(server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}

        self.assertEqual(set(tools), set(EXPECTED_TOOLS))
        self.assertEqual(len(tools), 113)
        for public_name in ("plot.render", "plot_render"):
            schema = tools[public_name].inputSchema
            self.assertEqual(set(schema["properties"]), {"execution", "options", "path", "solution"})
            self.assertIs(schema["additionalProperties"], False)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({"path": "pg", "execution": {}, "unknown_root_field": "visible"}, schema)
            jsonschema.validate({"path": "pg", "execution": {}}, schema)
            self.assertFalse(schema["properties"]["solution"]["additionalProperties"])
            for field in ("options", "solution"):
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate({"path": "pg", "execution": {}, field: None}, schema)


if __name__ == "__main__":
    unittest.main()
