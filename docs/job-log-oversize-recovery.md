# 超大 `job.log` 事件恢复 / Recovering an oversized `job.log` event

## 中文

先用 `operation_describe` 检查当前安装的 `job.log` 和 `job.log.event.read` schema，再通过 `operation_call` 分页读取。首次调用 `job.log` 时传 `job_id` 及当前 schema 允许的参数，不传 `cursor`；后续将上一页的十进制 `next_cursor` 原样作为 `cursor`。继续到 `events` 中出现 `record_type="event_gap"`。该项的 `id` 是原事件 ID；gap 只表示超限行没有内联返回，不证明任务终态或求解器日志完整。

对需要的每个原始字段单独调用 `job.log.event.read`，`field` 选 `metadata`、`event` 或 `created_at`。首次从 `offset=0` 开始，省略 `field_sha256`；续页使用上一页的 `next_offset` 和 `sha256`，每页 `limit` 不超过 49152 字节。

```json
{
  "operation_id": "job.log.event.read",
  "arguments": {
    "job_id": "<job-id>",
    "event_id": 123,
    "field": "metadata",
    "offset": 0,
    "limit": 49152
  },
  "execution": {
    "project_id": "<authorized-project-id>"
  }
}
```

逐页检查 `storage_type`、`is_sql_null`、`byte_length` 和 `sha256`，确认所有页的类型、NULL 标志、完整字段长度与摘要一致。按页顺序 Base64 解码 `chunk_base64` 并拼接原始字节；完成后确认字节数等于 `byte_length`，且拼接字节的 SHA-256 等于 `sha256`。

- SQL NULL 返回 `storage_type="null"`、`is_sql_null=true`，长度为 0。即使它的摘要与空字节相同，也不能当作空 TEXT 或空 BLOB。
- TEXT 返回 `storage_type="text"`、`is_sql_null=false`。仅当 `database_encoding` 为 UTF-8 时，才在拼接完整字段后按 UTF-8 解码；非空 TEXT 使用其他数据库编码时会返回 `EVENT_ENCODING_UNSUPPORTED`。
- BLOB 返回 `storage_type="blob"`、`is_sql_null=false`。保留拼接后的原始字节，不要默认按 UTF-8 解码。

页边界可能切开一个 UTF-8 多字节字符，NUL 字节也会保留。因此不要逐页解码文本；先拼接并校验完整字节，再按类型处理。

续页若返回 `EVENT_FIELD_CHANGED`，该错误带 `safe_retry=false` 且不返回该页字节。立即丢弃此字段此前收集的所有页并停止；不要自动重试。只有用户明确要求继续只读检查时，才另发一次读取，以 `offset=0` 且省略旧 `field_sha256` 开启新快照。新快照的页只能按新的 `sha256` 拼接，不能混用旧页。此路线只读取已持久化字段，绝不重放、重新派发或恢复任何 UNKNOWN job/operation；用户未明确要求继续时应停止。

`job.log` 与 `job.log.event.read` 都要求外层 `execution.project_id` 具有该项目的 `inspect` 授权，并校验 job 与 operation 属于同一项目；字段参数中不要传 `project_id`。跨项目 job 按 `NODE_NOT_FOUND` 拒绝。此路线只恢复已存储的事件字段，不替代仍 fail-closed 的 `solver.log_read`，不证明完整求解器输出，也不保证 SQLite、进程或调用方总内存有界。

## English

Check the installed `job.log` and `job.log.event.read` schemas with `operation_describe`, then page through them with `operation_call`. Start `job.log` with `job_id` and only parameters allowed by the live schema, omitting `cursor`. For each continuation, pass the previous page's decimal `next_cursor` unchanged. Continue until `events` contains `record_type="event_gap"`. Its `id` is the original event ID. A gap means the oversized row was omitted from the inline page; it does not establish a terminal job state or complete solver logs.

Read each needed raw field separately with `job.log.event.read`, setting `field` to `metadata`, `event`, or `created_at`. Start at `offset=0` and omit `field_sha256`. For each continuation, use the previous page's `next_offset` and `sha256`; `limit` must not exceed 49,152 bytes.

On every page, check `storage_type`, `is_sql_null`, `byte_length`, and `sha256`, and confirm that the type, NULL flag, complete-field length, and digest stay consistent. Base64-decode each `chunk_base64`, concatenate the original bytes in order, then confirm the combined length equals `byte_length` and its SHA-256 equals `sha256`.

- SQL NULL has `storage_type="null"`, `is_sql_null=true`, and length 0. Its digest may match the digest of empty bytes, but it is not an empty TEXT or BLOB.
- TEXT has `storage_type="text"` and `is_sql_null=false`. Decode it as UTF-8 only after concatenating the full field and only when `database_encoding` is UTF-8. Non-empty TEXT in another database encoding returns `EVENT_ENCODING_UNSUPPORTED`.
- BLOB has `storage_type="blob"` and `is_sql_null=false`. Preserve the concatenated raw bytes; do not implicitly decode them as UTF-8.

A page boundary may split a multibyte UTF-8 character, and NUL bytes are preserved. Do not decode text page by page; concatenate and verify the complete bytes first, then handle them according to their storage type.

If a continuation returns `EVENT_FIELD_CHANGED`, the error has `safe_retry=false` and contains no bytes for that page. Discard every earlier page collected for that field and stop; do not retry automatically. Only if the user explicitly asks to continue read-only inspection, issue a separate read starting at `offset=0` and omit the old `field_sha256` to open a new snapshot. Assemble pages from that snapshot only under its new `sha256`; never mix in old pages. This route only reads persisted fields and must never replay, redispatch, or recover an UNKNOWN job/operation. Stop if the user has not explicitly asked to continue.

Both `job.log` and `job.log.event.read` require an outer `execution.project_id` authorized for `inspect` and verify that the job and operation belong to that project. Do not pass `project_id` in the field-read arguments. A cross-project job is refused as `NODE_NOT_FOUND`. This route recovers stored event-field bytes only; it does not replace the still fail-closed `solver.log_read`, certify complete solver output, or guarantee a total SQLite, process, or caller-memory bound.
