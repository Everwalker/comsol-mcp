# C065 + `artifact.verify` 精确合并独立复审

**结论：PASS（精确合并的软件源码范围）。** 我在冻结候选的全新 APFS sparse/clonefile 副本上，独立核对了合并源码和 12 路径闭包，并在严格 guard 下完成完整 273 项联合选择器。未发现对已验收 V01–V06 verifier 或 C065 `model.compare` 路由、schema、五个叶文件的实质回归。

## 冻结身份和副本

- 冻结：`INTEGRATION_FREEZE001.json`，SHA-256 `7bc81d91cbd7115fc2c099d5b42215289436d04ac7e03592ef75b3bc9e823ada`。
- Handoff：`INTEGRATION_HANDOFF001.json`，SHA-256 `c596d3e28bcf6f0f71982a7a2a9b2a2d036f2be5727d3fec55f0c6631492667434a2b`。
- 精确六路径补丁：`INTEGRATION_DIFF001.patch`，SHA-256 `874f71eaa81ce154f89310970af82a1384560ada10db097432bb8d6a116bfd6f`。反向适用检查和 `git diff --check` 均通过，工作树恰为冻结的六个覆盖路径。
- Base commit `6ec9d888c24743509d984e874b262adcf9023e55`；独立重建的 Git 树 OID `1198d2aaca1c2828d926b1b3853cc6ecfe8ae5e2` 与冻结值相同。重建使用 base index 并只更新六个冻结覆盖路径。
- 12 路径源码闭包 SHA-256 指纹在冻结源、独立副本和最终测试后均为 `d4fa872557004c186d56613bcf18e17f8861889c6dbfb0367bc7d34c98c17d8a`。
- 最终测试副本 `/private/tmp/comsol-c065-artifact-verify-independent-joint-review002` 通过 `cp -cR` 在 `/System/Volumes/Data` APFS 上创建；副本和来源同设备，12/12 个闭包文件 inode 独立。

## 源码保持性

六个 postimage SHA-256 全部匹配冻结值。`_g2_artifact_verify.py`、`tests/test_artifact_verify.py`、`_g2_artifact_reads.py` 和 `tests/test_artifact_registration_import.py` 与此前独立接受的 V01–V06 候选逐字节相同；`_operation_store.py` 与 base blob 相同。

C065 的五个 compare 叶文件——`_execution_contract.py`、`_g2_checkpoint_diff.py`、`_g2_model_compare.py`、`_managed_backend.py`、`test_unit_c_model_compare.py`——各自 SHA-256 均与先前接受记录相同。合并前的 controller 和 registry base blobs 也分别与 C065 已接受源码哈希一致。对 controller/registry 内直接包含 `model.compare` 的条件分支进行字节比较均与 base 完全相同；修改 hunk 只扩展 artifact control/read/registry 路径，未更改 compare 路由或其 schema 分支。C065 compare 模块与对应测试在联合选择器中通过 31 项。两个 action catalog、`pyproject.toml` 和 `uv.lock` 均与 base 相同。

源码与合并差异核查记录见 `SOURCE_REVIEW001.json` 和 `MERGE_SHARED_DIFF.txt`；对应 SHA-256 分别为 `8cd4ef169474acdf670c77dda0135bea057361739f63453d7e930930c9cd5e57`、`1c387c339a483e9c6d9b7f1d54142673022d5fa4e264d8737be0b63a3ac4d17c`。

## 最终独立联合测试

最终证据是 `run002`。它执行八个冻结生产测试模块、旧的九项独立 verifier controls，以及一个 50,000 层 JSON real-route control：273 项通过，0 failures、0 errors、0 skips，返回码 0。原控制文件在执行前后均逐字节未变：旧九项 controls SHA-256 `205f616958f8936667a23df51048b37f6bcfc56ef58e99458ab6550ce529751d`；deep-JSON control SHA-256 `60c024c257e7f7a3859b850ec4b8df0003210a6511ce3d68662e158756049ba3`。

`test_artifact_verify_real_accepted_dev1_bundles_through_registration` 找到且未跳过。它把三个原始 dev1 ZIP 字节注册到合成项目，然后实际调用生产 `artifact.verify` 路由；每个目标、成员完整性、依赖闭包和静态 tag 检查通过。三个归档在独立测试前后 SHA-256 不变。没有安装、导入归档 payload 或启动真实 Worker。

固定 Python 3.12 runtime 和 guard 分别为 `/private/tmp/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python` 与既有 `guarded_entry.py` / `sitecustomize.py`。Guard 日志在子进程启动前于 `/private/tmp` 以 `0600` 预建；结果为 active=1、denied=0、setup_errors=0。HOME、TMPDIR、TMP、TEMP、pytest basetemp/cache 和 pycache 全在 clone 外的 `/private/tmp/comsol-c065-artifact-verify-independent-joint-run002/run002` 下。完整 argv、env、guard 源文件哈希和输出原始哈希已保存在 `run002/TEST_RUN_RECEIPT.json`。

- Receipt：SHA-256 `415eda406f0f1b0a3fb5a110c0eaaecc8e5e7d8ffa7c2f137a42a836d64781b5`。
- JUnit：SHA-256 `2b42f4fcd375906f448533f8ca13ba2ce9c430c0d9e68952f40810666232837d`。
- Guard：SHA-256 `e829f1d33850e6dcd129e9deed10365b7d3c160948e9f34529d10b776bf378ad`。
- Stdout：SHA-256 `6c2eb851819c44f8bc8eb0629caa774503beb47de092f2b4f39c4140cfdd926b`；stderr 为空，SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。
- 12 路径准备记录：`PREPARATION_RECEIPT002.json`，SHA-256 `2b464b4cc5d820c5bd6854ca8d89a9cd74a71edb1bfdb40ddba1887207cb7403`。

## 保留的历史和初次独立尝试

原始 producer 证据未覆盖：`joint-run001` JUnit 为 273 项、3 failures、1 skip（exFAT basetemp 的 AppleDouble / `os.link` 环境问题）；`joint-run002` 为 273 项、0 failure、1 skip（没有传入 `COMSOL_ARTIFACT_VERIFY_BUNDLE_PATHS`，所以真实归档路线未执行）；`joint-run003` 为 273 项全通过，包含显式归档路径和真实注册路线。三组 receipt、JUnit、guard 和对应说明仍保留在原目录。

第一次独立执行 `run001` 也通过 273 项，但其 pytest 临时目录放在独立副本内部的 sparse-checkout 排除路径。源码 12 路径指纹仍未变化；该运行作为 harness 放置失误保留并由 `RUN001_INTERPRETATION_ADDENDUM001.json` 说明，最终结论采用 clone 外临时目录的全新副本 `run002`。

## 验收边界

本结论只接受精确合并源码和软件层联合测试。Native compatibility、COMSOL/JVM、科学有效性、full-goal 和 release 均为 `NOT_ACCEPTED`。本次未改动 live 工作树，未提交、推送或发布，也未安装依赖或运行网络操作。
