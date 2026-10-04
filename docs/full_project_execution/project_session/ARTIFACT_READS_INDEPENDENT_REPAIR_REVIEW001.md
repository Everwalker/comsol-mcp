# artifact.list / artifact.inspect 修复候选独立复审

**结论：PASS（仅限本报告所列离线合成软件测试）。** 本结论绑定完整候选树指纹 SHA-256 `b32762b3e2ee52f44882ae3fd904342c20149fdb038ad16d2d403f0d97ded8fe`，基线 commit `607516192f94a33aa5e0b2788d2f8882d65510c9`。修复 handoff SHA-256 为 `a5dfc4aedec28bc5d2623596580a662951b2e9e00cfc99521bdb5f1d51e684e9`，patch SHA-256 为 `d9a19925344f3f987610e59f2d9f16e277f843a4881c5117cc9f2318b21b20cd`。四个 patch 路径与 handoff 相符；原候选正向 patch dry-run 和修复候选反向 dry-run 均返回 0。

复审在 `/private/tmp/comsol-artifact-control-reads-repair-independent-review001` 的 APFS 独立副本上进行。对完整实际候选树只生成了一次指纹，包含相对路径、对象类型、权限位、普通文件大小和内容 SHA-256、符号链接目标；树含 11,292 个普通文件、2,470 个目录、25 个符号链接，普通文件共 1,912,390,039 字节。独立副本与源同设备，五个源文件 inode 均不同，五源哈希和布局一致。树清单文件自身 SHA-256 为 `3bfaf95fc0a4071d2c85dc6cad65b6bac9681acd6c73322f3526b38b2be0d982`。

冻结候选的五个源文件如下；测试前后哈希完全相同：

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `comsol_mcp/_control_daemon.py`（保持原冻结版本） | 727955 | `9b719b62a88e8df3f4f95e2c8f0d66baf044e6e9884e3f33a1e92f88eb3595d7` |
| `comsol_mcp/_g2_artifact_reads.py` | 18949 | `b5f48c6f27d3fd017ae91edbc9f9b54739ad109dc61d552ccc25bf896099bb67` |
| `comsol_mcp/_g2_registry.py` | 82554 | `8af622f9e85950fc07e10fe62172f503943de01e3dd8dea083a3d31f100a17e3` |
| `comsol_mcp/_operation_store.py` | 226849 | `d9d6c3d54ff4afc625f28f5e70595fd23f6a899e05863a9cdf0b05711a0808fa` |
| `tests/test_artifact_registration_import.py` | 52448 | `99c1f3d64697fb183ba2d124052d802813df4b48b1d95760b139e306a336d4b8` |

四个初次复审 finding 的前后状态：

1. **symlink 错误泄漏绝对路径：FAIL → PASS。** 初版 resolver 将 managed path 放入异常文本，control 响应直接传出；修复后对已知 `ACCESS_VIOLATION` / `ARTIFACT_NOT_FOUND` 返回固定安全信息并保留错误码。原对抗用例通过。
2. **损坏的 `format_version` 可含绝对路径：FAIL → PASS。** 初版 inspect 原样返回字符串；修复后读取时校验 `role`、`classification`、`artifact_type`、`format_version` 的长度、控制字符和路径型内容，拒绝不安全值；安全 MIME/命名空间值的正向用例保留通过。原对抗用例通过。
3. **list 可返回非 SHA-256 的 scoped key：FAIL → PASS。** 初版对数据库返回的 key 未执行十六进制格式验证；修复后要求 64 位小写十六进制标识，不合法记录 fail-closed。`g` 重复 64 次的原对抗用例通过。
4. **list 字段与冻结计划不符：FAIL → PASS。** 初版 list 输出 `format_version_status`，冻结计划要求 `version_status`；修复后 list 实现与 registry schema 使用 `version_status`，inspect 仍使用计划规定的 `format_version_status`。原冻结契约用例通过。

在严格网络 guard 下，仅运行完整 `tests/test_artifact_registration_import.py` 与原样 reviewer-owned adversarial 测试模块。指定 Python 3.12 runtime，timeout 90 秒；实际 2.473 秒，38/38 PASS，0 failures、0 errors、0 skipped，stderr 为空。guard 在子入口前激活 1 次，denied 0，setup error 0。四个原 reviewer 对抗断言均通过。运行收据 SHA-256 `3d397c04aa4649b9136f67269c7d49c4d41ac50688f76ad307c7e7568c119fa5`；JUnit SHA-256 `6e1ca04aa7babf39b1c475baa5a201c230442e5857bb9c108a06e6e19e3791a0`；guard 日志 SHA-256 `af234c4d6bba26a6556dbc62ca46f792269c89c6ed360f28823ea4e98bb4d8c3`。完整命令、源文件前后哈希及输出见 `TEST_RUN_RECEIPT.json`。

**保留的失败与准备记录：** 初次独立复审候选指纹为 `17752cde552e2efb157ac781b78e74fae90ec85f5f3c99ab705403d6705f5b16`，31 项测试为 27 PASS、4 FAIL；原 `INDEPENDENT_REVIEW_REPORT.md`、JUnit、stdout、stderr、guard、run receipt 均仍在 `execution-scratch/artifact-control-reads-20261005-001/independent-list-inspect-review001/`，未覆盖。该目录的 `PREPARATION_ATTEMPT001.json` 也保留：一次输入标签把 checkpoint SHA-256 当作 SHA-1 比较，预检退出且未创建或修改候选；实际 SHA-256 为 `1b61fb472e8b53d646286cd1f8592aca2ed615db8ef38ad5c98fe5ff6e633920`，实际 SHA-1 为 `c572cf04e0c43c00433eb75d64b8ffb3d0be3ed6`。修复复审的准备和测试收据分别为 `PREPARATION_RECEIPT.json` 与 `TEST_RUN_RECEIPT.json`。

**非阻塞 cursor 观察：** 初次复审发现 cursor 是未签名的 base64 JSON；调用者可改动有效 `after_artifact_id` 来跳过项目。每次请求仍重新鉴权并以项目/根目录/host/engine 精确限定 SQL，未发现跨 scope 读取；冻结计划将 cursor 定义为 continuation hint，而非授权凭证。此项不是本次四个修复 finding，修复 patch 未涉及 cursor，也不构成本次测试失败。

验收范围仅限上述两个测试模块覆盖的隔离合成软件行为。真实 Worker 启动、JVM、COMSOL/native、安装和外部网络均为 `NOT_RUN`；本 PASS 不代表它们或科学验收通过。
