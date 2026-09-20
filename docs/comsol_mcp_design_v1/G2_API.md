# COMSOL MCP G2 API

This file is generated from `02_ACTION_CATALOG.json` by `tools/generate_g2_api.py`.
It describes the W08-W12 control surface; `SUPPORTED_UNVERIFIED` means the software route exists and still requires the recorded COMSOL runtime evidence.
Published `input_schema` and `output_schema` describe the effective production wire. `catalog_input_schema` is retained for traceability; it is not an assertion that the historical catalog model_ref string or ActionResult(ok=...) shape is accepted unchanged.

## Wire envelope

Model calls use `registry_call(operation_id, arguments, execution)` (or `operation_call`). The nested operation is validated again at the control daemon. `execution` carries the session, model reference, expected revision, idempotency key, and wait budgets. Responses retain a structured `success` boolean, `data`, `error`, partial/unknown signals, and execution metadata; MCP transport sets `isError` from `success`.

## Executable catalog

| Operation | MCP fallback | Domain | Effect | Scope | Gate | Status |
|---|---|---|---|---|---|---|
| `checkpoint.create` | `checkpoint_create` | checkpoint | `FILE_WRITE` | `model` | `G1` | `SUPPORTED_UNVERIFIED` |
| `checkpoint.inspect` | `checkpoint_inspect` | checkpoint | `READ` | `project` | `G1` | `SUPPORTED_UNVERIFIED` |
| `checkpoint.list` | `checkpoint_list` | checkpoint | `READ` | `project` | `G1` | `SUPPORTED_UNVERIFIED` |
| `checkpoint.restore` | `checkpoint_restore` | checkpoint | `WRITE` | `model` | `G4` | `SUPPORTED_UNVERIFIED` |
| `code.compile_java` | `code_compile_java` | code | `COMPUTE` | `project` | `G2` | `SUPPORTED_UNVERIFIED` |
| `code.describe_java` | `code_describe_java` | code | `READ` | `project` | `G2` | `SUPPORTED_UNVERIFIED` |
| `code.execute_java` | `code_execute_java` | code | `TRUSTED_CODE` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |
| `code.inspect_run` | `code_inspect_run` | code | `READ` | `project` | `G2` | `SUPPORTED_UNVERIFIED` |
| `docs.error_search` | `docs_error_search` | docs | `READ` | `project` | `G4` | `SUPPORTED_UNVERIFIED` |
| `docs.examples` | `docs_examples` | docs | `READ` | `project` | `G2` | `SUPPORTED_UNVERIFIED` |
| `docs.get` | `docs_get` | docs | `READ` | `project` | `G2` | `SUPPORTED_UNVERIFIED` |
| `docs.index` | `docs_index` | docs | `STATE_WRITE` | `project` | `G2` | `SUPPORTED_UNVERIFIED` |
| `docs.search` | `docs_search` | docs | `READ` | `project` | `G2` | `SUPPORTED_UNVERIFIED` |
| `node.children` | `node_children` | node | `READ` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |
| `node.find` | `node_find` | node | `READ` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |
| `node.inspect` | `node_inspect` | node | `READ` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |
| `node.property_entry_set` | `node_property_entry_set` | node | `WRITE` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |
| `node.property_get` | `node_property_get` | node | `READ` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |
| `node.property_index_set` | `node_property_index_set` | node | `WRITE` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |
| `node.property_schema` | `node_property_schema` | node | `READ` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |
| `node.property_set` | `node_property_set` | node | `WRITE` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |
| `registry.call` | `registry_call` | registry | `DYNAMIC` | `control` | `G2` | `SUPPORTED_UNVERIFIED` |
| `registry.describe` | `registry_describe` | registry | `READ` | `control` | `G2` | `SUPPORTED_UNVERIFIED` |
| `registry.list` | `registry_list` | registry | `READ` | `control` | `G2` | `SUPPORTED_UNVERIFIED` |
| `registry.manifest` | `registry_manifest` | registry | `READ` | `control` | `G2` | `SUPPORTED_UNVERIFIED` |
| `registry.search` | `registry_search` | registry | `READ` | `control` | `G2` | `SUPPORTED_UNVERIFIED` |
| `transaction.apply` | `transaction_apply` | transaction | `WRITE` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |
| `transaction.preview` | `transaction_preview` | transaction | `READ` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |
| `transaction.recover` | `transaction_recover` | transaction | `WRITE` | `model` | `G4` | `SUPPORTED_UNVERIFIED` |
| `transaction.trial` | `transaction_trial` | transaction | `COMPUTE` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |
| `transaction.verify` | `transaction_verify` | transaction | `EVALUATE` | `model` | `G2` | `SUPPORTED_UNVERIFIED` |

## Operation schemas

The following JSON object is generated from the catalog and includes every executable W08-W12 operation.

```json
{
  "checkpoint.create": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "include_solution": {
          "type": "boolean"
        },
        "label": {
          "type": "string"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "label"
      ],
      "title": "checkpoint.create input",
      "type": "object"
    },
    "effect": "FILE_WRITE",
    "gate": "G1",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "include_solution": {
          "type": "boolean"
        },
        "label": {
          "type": "string"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "label"
      ],
      "title": "checkpoint.create input",
      "type": "object"
    },
    "mcp_tool_name": "checkpoint_create",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful",
      "readback_and_revision",
      "idempotency_and_partial_failure"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "checkpoint.inspect": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "checkpoint_id": {
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "checkpoint_id"
      ],
      "title": "checkpoint.inspect input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G1",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "checkpoint_id": {
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "checkpoint_id"
      ],
      "title": "checkpoint.inspect input",
      "type": "object"
    },
    "mcp_tool_name": "checkpoint_inspect",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "project",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "checkpoint.list": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "filter": {
          "type": "object"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [
        "project_id"
      ],
      "title": "checkpoint.list input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G1",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "filter": {
          "type": "object"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [
        "project_id"
      ],
      "title": "checkpoint.list input",
      "type": "object"
    },
    "mcp_tool_name": "checkpoint_list",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "project",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "checkpoint.restore": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "authorization_ref": {
          "type": "string"
        },
        "checkpoint_id": {
          "type": "string"
        },
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "checkpoint_id"
      ],
      "title": "checkpoint.restore input",
      "type": "object"
    },
    "effect": "WRITE",
    "gate": "G4",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "authorization_ref": {
          "type": "string"
        },
        "checkpoint_id": {
          "type": "string"
        },
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "checkpoint_id"
      ],
      "title": "checkpoint.restore input",
      "type": "object"
    },
    "mcp_tool_name": "checkpoint_restore",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful",
      "readback_and_revision",
      "idempotency_and_partial_failure"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "code.compile_java": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "entrypoint": {
          "type": "string"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "runtime_id": {
          "type": "string"
        },
        "source_artifact": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "idempotency_key",
        "runtime_id",
        "source_artifact",
        "entrypoint"
      ],
      "title": "code.compile_java input",
      "type": "object"
    },
    "effect": "COMPUTE",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "entrypoint": {
          "type": "string"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "runtime_id": {
          "type": "string"
        },
        "source_artifact": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "idempotency_key",
        "runtime_id",
        "source_artifact",
        "entrypoint"
      ],
      "title": "code.compile_java input",
      "type": "object"
    },
    "mcp_tool_name": "code_compile_java",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "project",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "code.describe_java": {
    "catalog_input_schema": {
      "additionalProperties": false,
      "properties": {
        "entrypoint": {
          "type": "string"
        },
        "project_id": {
          "type": "string"
        },
        "source_artifact": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "source_artifact"
      ],
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "additionalProperties": false,
      "properties": {
        "entrypoint": {
          "type": "string"
        },
        "project_id": {
          "type": "string"
        },
        "source_artifact": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "source_artifact"
      ],
      "type": "object"
    },
    "mcp_tool_name": "code_describe_java",
    "output_contract": "ActionResult",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path"
    ],
    "scope": "project",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "code.execute_java": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "arguments": {
          "type": "object"
        },
        "entrypoint": {
          "type": "string"
        },
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "invariants": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "mode": {
          "type": "string"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        },
        "source_artifact": {
          "type": "string"
        },
        "timeout_s": {
          "type": "number"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "source_artifact",
        "entrypoint",
        "arguments",
        "mode"
      ],
      "title": "code.execute_java input",
      "type": "object"
    },
    "effect": "TRUSTED_CODE",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "arguments": {
          "type": "object"
        },
        "entrypoint": {
          "type": "string"
        },
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "invariants": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "mode": {
          "type": "string"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        },
        "source_artifact": {
          "type": "string"
        },
        "timeout_s": {
          "type": "number"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "source_artifact",
        "entrypoint",
        "arguments",
        "mode"
      ],
      "title": "code.execute_java input",
      "type": "object"
    },
    "mcp_tool_name": "code_execute_java",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful",
      "readback_and_revision",
      "idempotency_and_partial_failure"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "code.inspect_run": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "job_id": {
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "job_id"
      ],
      "title": "code.inspect_run input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "job_id": {
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "job_id"
      ],
      "title": "code.inspect_run input",
      "type": "object"
    },
    "mcp_tool_name": "code_inspect_run",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "project",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "docs.error_search": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "error": {
          "type": "string"
        },
        "node_type": {
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "version": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "error",
        "version"
      ],
      "title": "docs.error_search input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G4",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "error": {
          "type": "string"
        },
        "node_type": {
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "version": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "error",
        "version"
      ],
      "title": "docs.error_search input",
      "type": "object"
    },
    "mcp_tool_name": "docs_error_search",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "project",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "docs.examples": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "query": {
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "version": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "query",
        "version"
      ],
      "title": "docs.examples input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "query": {
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "version": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "query",
        "version"
      ],
      "title": "docs.examples input",
      "type": "object"
    },
    "mcp_tool_name": "docs_examples",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "project",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "docs.get": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "document_ref": {
          "type": "string"
        },
        "length": {
          "type": "integer"
        },
        "offset": {
          "type": "integer"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "section": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "document_ref"
      ],
      "title": "docs.get input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "document_ref": {
          "type": "string"
        },
        "length": {
          "type": "integer"
        },
        "offset": {
          "type": "integer"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "section": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "document_ref"
      ],
      "title": "docs.get input",
      "type": "object"
    },
    "mcp_tool_name": "docs_get",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "project",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "docs.index": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "runtime_id": {
          "type": "string"
        },
        "sources": {
          "items": {
            "type": "string"
          },
          "type": "array"
        }
      },
      "required": [
        "project_id",
        "idempotency_key",
        "runtime_id",
        "sources"
      ],
      "title": "docs.index input",
      "type": "object"
    },
    "effect": "STATE_WRITE",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "product": {
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "runtime_id": {
          "type": "string"
        },
        "sources": {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        "version": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "idempotency_key",
        "runtime_id",
        "sources"
      ],
      "title": "docs.index input",
      "type": "object"
    },
    "mcp_tool_name": "docs_index",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "project",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "docs.search": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "limit": {
          "type": "integer"
        },
        "product": {
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "query": {
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "version": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "query",
        "version"
      ],
      "title": "docs.search input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "limit": {
          "type": "integer"
        },
        "product": {
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "query": {
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "version": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "query",
        "version"
      ],
      "title": "docs.search input",
      "type": "object"
    },
    "mcp_tool_name": "docs_search",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "project",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "node.children": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "cursor": {
          "type": "string"
        },
        "limit": {
          "type": "integer"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "path"
      ],
      "title": "node.children input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "cursor": {
          "type": "string"
        },
        "limit": {
          "type": "integer"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "path"
      ],
      "title": "node.children input",
      "type": "object"
    },
    "mcp_tool_name": "node_children",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "node.find": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "limit": {
          "type": "integer"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "query": {
          "type": "object"
        },
        "request_id": {
          "type": "string"
        },
        "root": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "query"
      ],
      "title": "node.find input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "limit": {
          "type": "integer"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "query": {
          "type": "object"
        },
        "request_id": {
          "type": "string"
        },
        "root": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "query"
      ],
      "title": "node.find input",
      "type": "object"
    },
    "mcp_tool_name": "node_find",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "node.inspect": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "include_values": {
          "type": "boolean"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "path"
      ],
      "title": "node.inspect input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "include_values": {
          "type": "boolean"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "path"
      ],
      "title": "node.inspect input",
      "type": "object"
    },
    "mcp_tool_name": "node_inspect",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "node.property_entry_set": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "key": {
          "type": "string"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "name": {
          "type": "string"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        },
        "value": {
          "$ref": "common.schema.json#/$defs/TypedValue"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "path",
        "name",
        "key",
        "value"
      ],
      "title": "node.property_entry_set input",
      "type": "object"
    },
    "effect": "WRITE",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "key": {
          "type": "string"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "name": {
          "type": "string"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        },
        "value": {
          "$ref": "common.schema.json#/$defs/TypedValue"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "path",
        "name",
        "key",
        "value"
      ],
      "title": "node.property_entry_set input",
      "type": "object"
    },
    "mcp_tool_name": "node_property_entry_set",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful",
      "readback_and_revision",
      "idempotency_and_partial_failure"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "node.property_get": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "names": {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "path",
        "names"
      ],
      "title": "node.property_get input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "names": {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "path",
        "names"
      ],
      "title": "node.property_get input",
      "type": "object"
    },
    "mcp_tool_name": "node_property_get",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "node.property_index_set": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "indices": {
          "items": {
            "type": "integer"
          },
          "type": "array"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "name": {
          "type": "string"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        },
        "value": {
          "$ref": "common.schema.json#/$defs/TypedValue"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "path",
        "name",
        "indices",
        "value"
      ],
      "title": "node.property_index_set input",
      "type": "object"
    },
    "effect": "WRITE",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "indices": {
          "items": {
            "type": "integer"
          },
          "type": "array"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "name": {
          "type": "string"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        },
        "value": {
          "$ref": "common.schema.json#/$defs/TypedValue"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "path",
        "name",
        "indices",
        "value"
      ],
      "title": "node.property_index_set input",
      "type": "object"
    },
    "mcp_tool_name": "node_property_index_set",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful",
      "readback_and_revision",
      "idempotency_and_partial_failure"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "node.property_schema": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "name": {
          "type": "string"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "path"
      ],
      "title": "node.property_schema input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "name": {
          "type": "string"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "path"
      ],
      "title": "node.property_schema input",
      "type": "object"
    },
    "mcp_tool_name": "node_property_schema",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "node.property_set": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "properties": {
          "$ref": "common.schema.json#/$defs/PropertySet"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "path",
        "properties"
      ],
      "title": "node.property_set input",
      "type": "object"
    },
    "effect": "WRITE",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "path": {
          "$ref": "common.schema.json#/$defs/NodePath"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "properties": {
          "$ref": "common.schema.json#/$defs/PropertySet"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "path",
        "properties"
      ],
      "title": "node.property_set input",
      "type": "object"
    },
    "mcp_tool_name": "node_property_set",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful",
      "readback_and_revision",
      "idempotency_and_partial_failure"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "registry.call": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "arguments": {
          "type": "object"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "operation_id": {
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [
        "idempotency_key",
        "operation_id",
        "arguments"
      ],
      "title": "registry.call input",
      "type": "object"
    },
    "effect": "DYNAMIC",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "arguments": {
          "type": "object"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "operation_id": {
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [
        "idempotency_key",
        "operation_id",
        "arguments"
      ],
      "title": "registry.call input",
      "type": "object"
    },
    "mcp_tool_name": "registry_call",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "control",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "registry.describe": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "operation_id": {
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [
        "operation_id"
      ],
      "title": "registry.describe input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "operation_id": {
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [
        "operation_id"
      ],
      "title": "registry.describe input",
      "type": "object"
    },
    "mcp_tool_name": "registry_describe",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "control",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "registry.list": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "cursor": {
          "type": "string"
        },
        "domain": {
          "type": "string"
        },
        "limit": {
          "type": "integer"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [],
      "title": "registry.list input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "cursor": {
          "type": "string"
        },
        "domain": {
          "type": "string"
        },
        "limit": {
          "type": "integer"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [],
      "title": "registry.list input",
      "type": "object"
    },
    "mcp_tool_name": "registry_list",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "control",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "registry.manifest": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "profile": {
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [],
      "title": "registry.manifest input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "profile": {
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [],
      "title": "registry.manifest input",
      "type": "object"
    },
    "mcp_tool_name": "registry_manifest",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "control",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "registry.search": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "domain": {
          "type": "string"
        },
        "query": {
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [
        "query"
      ],
      "title": "registry.search input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "domain": {
          "type": "string"
        },
        "query": {
          "type": "string"
        },
        "request_id": {
          "type": "string"
        }
      },
      "required": [
        "query"
      ],
      "title": "registry.search input",
      "type": "object"
    },
    "mcp_tool_name": "registry_search",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "control",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "transaction.apply": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "actions": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "checkpoint_policy": {
          "type": "string"
        },
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "invariants": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "actions",
        "checkpoint_policy"
      ],
      "title": "transaction.apply input",
      "type": "object"
    },
    "effect": "WRITE",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "actions": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "checkpoint_policy": {
          "type": "string"
        },
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "invariants": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "actions",
        "checkpoint_policy"
      ],
      "title": "transaction.apply input",
      "type": "object"
    },
    "mcp_tool_name": "transaction_apply",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful",
      "readback_and_revision",
      "idempotency_and_partial_failure"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "transaction.preview": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "actions": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "invariants": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "actions"
      ],
      "title": "transaction.preview input",
      "type": "object"
    },
    "effect": "READ",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "actions": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "invariants": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "actions"
      ],
      "title": "transaction.preview input",
      "type": "object"
    },
    "mcp_tool_name": "transaction_preview",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "transaction.recover": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "authorization_ref": {
          "minLength": 1,
          "type": "string"
        },
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        },
        "strategy": {
          "type": "string"
        },
        "transaction_id": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "transaction_id",
        "strategy"
      ],
      "title": "transaction.recover input",
      "type": "object"
    },
    "effect": "WRITE",
    "gate": "G4",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "authorization_ref": {
          "minLength": 1,
          "type": "string"
        },
        "expected_revision": {
          "minimum": 0,
          "type": "integer"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        },
        "strategy": {
          "type": "string"
        },
        "transaction_id": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "transaction_id",
        "strategy"
      ],
      "title": "transaction.recover input",
      "type": "object"
    },
    "mcp_tool_name": "transaction_recover",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful",
      "readback_and_revision",
      "idempotency_and_partial_failure"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "transaction.trial": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "actions": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "checkpoint_id": {
          "description": "Explicit immutable checkpoint bound to the current model ref, revision, and fingerprint",
          "minLength": 1,
          "type": "string"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "invariants": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "idempotency_key",
        "actions"
      ],
      "title": "transaction.trial input",
      "type": "object"
    },
    "effect": "COMPUTE",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "actions": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "checkpoint_id": {
          "description": "Explicit immutable checkpoint bound to the current model ref, revision, and fingerprint",
          "minLength": 1,
          "type": "string"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "invariants": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "idempotency_key",
        "actions"
      ],
      "title": "transaction.trial input",
      "type": "object"
    },
    "mcp_tool_name": "transaction_trial",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  },
  "transaction.verify": {
    "catalog_input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "checks": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "model_ref": {
          "minLength": 1,
          "type": "string"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        },
        "transaction_id": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "idempotency_key",
        "transaction_id"
      ],
      "title": "transaction.verify input",
      "type": "object"
    },
    "effect": "EVALUATE",
    "gate": "G2",
    "implementation_status": "SUPPORTED_UNVERIFIED",
    "input_schema": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "additionalProperties": false,
      "properties": {
        "checks": {
          "items": {
            "type": "object"
          },
          "type": "array"
        },
        "idempotency_key": {
          "minLength": 1,
          "type": "string"
        },
        "model_ref": {
          "additionalProperties": false,
          "description": "Production ModelRef object; legacy catalog string is not used by the managed wire.",
          "properties": {
            "generation": {
              "minimum": 1,
              "type": "integer"
            },
            "model_tag": {
              "minLength": 1,
              "type": "string"
            },
            "schema_version": {
              "minimum": 1,
              "type": "integer"
            },
            "server_instance_id": {
              "minLength": 1,
              "type": "string"
            },
            "session_id": {
              "minLength": 1,
              "type": "string"
            }
          },
          "required": [
            "schema_version",
            "session_id",
            "server_instance_id",
            "model_tag",
            "generation"
          ],
          "type": "object"
        },
        "project_id": {
          "minLength": 1,
          "type": "string"
        },
        "request_id": {
          "type": "string"
        },
        "session_id": {
          "minLength": 1,
          "type": "string"
        },
        "transaction_id": {
          "type": "string"
        }
      },
      "required": [
        "project_id",
        "session_id",
        "model_ref",
        "idempotency_key",
        "transaction_id"
      ],
      "title": "transaction.verify input",
      "type": "object"
    },
    "mcp_tool_name": "transaction_verify",
    "output_contract": "ActionResult; add domain-specific data schema before implementation gate is closed",
    "output_schema": {
      "additionalProperties": true,
      "properties": {
        "data": {
          "type": "object"
        },
        "error": {
          "type": [
            "object",
            "string",
            "null"
          ]
        },
        "execution": {
          "type": "object"
        },
        "success": {
          "type": "boolean"
        }
      },
      "required": [
        "success",
        "data"
      ],
      "type": "object"
    },
    "required_tests": [
      "schema_valid",
      "normal_path",
      "failure_is_truthful"
    ],
    "scope": "model",
    "wire_compatibility": {
      "execution_fields": [
        "session_id",
        "model_ref",
        "expected_revision",
        "idempotency_key",
        "request_id",
        "rpc_timeout_s",
        "queue_timeout_s",
        "execution_timeout_s"
      ],
      "model_ref": "structured ModelRef object {schema_version,session_id,server_instance_id,model_tag,generation}; catalog string form is accepted only as historical metadata and is not the production wire shape",
      "request_execution": "execution is a keyword-only MCP field; registry_call/operation_call carries the same fields in its outer request",
      "result_fields": [
        "success",
        "data",
        "error",
        "execution"
      ],
      "transport": "MCP structuredContent plus text mirror; isError equals not success"
    }
  }
}
```

## Explicit limitations

- Registry publication does not establish COMSOL engine acceptance.
- Model writes, trial, restore, and trusted Java execution require the current control service revision gate and a live task-owned isolation receipt with an exact IPv4 loopback listener and connected-client observation.
- Java trusted code is a reviewed source path with SHA-256 and diagnostics; it is not an OS sandbox.
- Offline help is local, version-separated, source-hashed data. Missing versions return `UNAVAILABLE` and unknown search hits return `NOT_FOUND`.
