# 相对路径契约：多机同步下的路径可移植性设计

**Date**: 2026-09-11
**Status**: Implemented (Phases A–D on `feat/relative-paths-contract`; design decisions and as-built adjustments below)
**Purpose**: 统一 `.manifest.json` 源 key、页面 frontmatter `sources:`、以及项目 `source_cwd` 的路径表示，使同一个 vault 在多台机器间同步、查阅、再摄取时不再把机器绝对路径写回数据。

---

## Summary

引入 **路径 key 契约 v2**：在持久化数据中只允许三种 key 形式 —— vault 相对路径、`~` 相对路径、命名空间伪 key —— 并禁止写入裸机器绝对路径。所有比较在读取时统一归一为绝对路径（展开 `~`/环境变量、vault 相对 key 以 vault 根解析）后再进行。

契约的**唯一权威定义**放在 `.skills/llm-wiki/SKILL.md`，其余 skill 只引用、不复述。代码侧在写入路径上做归一（`cache.py` 的 `update_source`、`scripts/manifest.py`），读取侧保持对既有绝对 key 的完全兼容，因此不需要对现网 vault 做强制一次性迁移。迁移以显式的 `manifest.py migrate` 命令提供，可 dry-run、可回滚。

框架镜像目录（`.claude/`、`.cursor/`、`.agents/`、`.kiro/`、`.pi/`、`.windsurf/`）全部是指向 `.skills/` 的符号链接，文档改动只需落在 `.skills/` 一处即可全平台生效。

---

## Context

### 现状（已验证）

当前仓库存在两套互相冲突的路径约定。一套是 Vault 侧要求的"相对 vault 根"，另一套是框架母版明确要求的"展开后的绝对路径"。

| 组件 | 当前行为 | 位置 |
| --- | --- | --- |
| `llm-wiki`（架构母版） | **命令式规定** source key 必须是展开 `~` 和环境变量后的绝对路径 | `.skills/llm-wiki/SKILL.md:155` |
| `wiki-status` | 断言 "Source keys are canonical absolute paths"，示例 key 为 `/absolute/path/to/file.md`、`/Users/name/.claude/...` | `.skills/wiki-status/SKILL.md:32,39,48,60` |
| `wiki-update` | manifest 示例 `"source_cwd": "/absolute/path/to/project"`；log 模板 `source_cwd=/path/to/project` | `.skills/wiki-update/SKILL.md:202,219` |
| `wiki-ingest` | Step 7 只文档化 manifest 字段名，未声明 key 必须是 vault 相对路径；frontmatter `sources:` 无路径格式说明 | `.skills/wiki-ingest/SKILL.md:462-476` |
| `claude-history-ingest` | 复述 "manifest keys are absolute paths with `~` expanded"，并把 `manifest.py normalize` 作为标准修复手段 | `.skills/claude-history-ingest/SKILL.md:38` |
| `wiki-query` | 把 `source_cwd` 当作 "authoritative path"，要求答案中输出该绝对路径，并据此命名待改文件 | `.skills/wiki-query/SKILL.md:274-277,302` |
| `wiki-rebuild` | 归档元数据示例 `"vault_path": "/Users/name/Knowledge"` | `.skills/wiki-rebuild/SKILL.md:59` |
| 运行时代码 `cache.py` | 读取时已兼容相对 key：`_same_source` / `_missing_on_disk` 把相对 key 以 vault 根解析后再比较；写入时**原样保存**调用方传入的路径字符串 | `obsidian_wiki/cache.py:204-232,347,361` |
| 辅助脚本 `manifest.py` | 反向强制绝对：`canonical()` 无条件 `abspath`；`normalize` 把 key 重写为绝对路径并"警告但保留"相对 key；`delta` 输出绝对路径 | `scripts/manifest.py:8-11,31-32,69-76,171,183` |
| 文档镜像 | 六套镜像目录均为相对符号链接，指向 `.skills/` 单一来源，由 `setup.sh` 的 `install_skills` 维护 | `setup.sh:36-98` |
| `lint.py` | 只校验 frontmatter 是否存在 `sources` 字段，不校验其路径形态 | `obsidian_wiki/lint.py:26,253-261` |
| 其它历史 skill | 已存在 `~` 相对写法，例如 `source_path: ~/.claude/...`、`"source_path": "~/.hermes/"` | `.skills/llm-wiki/SKILL.md:95`、`.skills/claude-history-ingest/SKILL.md:397`、`.skills/hermes-history-ingest/SKILL.md:175` |

源数据在磁盘上存在两种拓扑，这是设计的关键约束：

- **T1 源在 vault 内**：例如 `Raw/database/xxx.pdf`、`Clippings/xxx.md`。可以用 vault 相对路径精确表示。
- **T2 源在 vault 外**：由 `OBSIDIAN_SOURCES_DIR` 指向的外部文档目录、`~/.claude/`、`~/.codex/` 等 agent 历史、以及被 `wiki-update` 同步的项目目录。这类源**物理上无法**用 vault 相对路径表达。

`llm-wiki/SKILL.md:20` 明确说明源文档"live wherever the user keeps them"，即 T2 是常态而非边缘情况。

### 问题陈述

1. **契约正面冲突**：Vault 侧的 AGENTS.md 规定 manifest key、frontmatter `sources`、wikilink 一律相对 vault 根，禁止机器绝对路径；框架母版 `llm-wiki:155` 却规定必须使用绝对路径。按 skill 文档操作会直接违反 vault 契约。
2. **多机复现**：绝对路径携带用户名和挂载点（`/Users/name/...` vs `/home/name/...`），同一 vault 在另一台机器上要么 delta 失配导致重复摄取，要么把错误的绝对路径写回页面与 manifest。这不是一次性迁移能解决的 —— 只要 skill 文档仍教绝对路径，每次按文档操作都会重新引入。
3. **代码与文档互相矛盾**：`cache.py` 已经把相对 key 当作一等公民，而 `scripts/manifest.py` 仍在反向强制绝对路径。两者对"规范形态"的定义不一致，使得文档无论怎么写都会与其中一个实现相悖。

### 约束与决策驱动

- **可移植性优先**：跨机器共享是首要场景，机器绝对路径必须从持久化数据中消失，而不是靠每台机器各自修复。
- **兼容既有 vault**：现网 vault 的 manifest 里已有大量绝对 key，不能要求用户停机做全量迁移；读取必须向后兼容。
- **不改变摄取拓扑**：方案不得要求用户把外部源复制进 vault。复制会引入数据重复、占用空间，且 agent 历史这类源并不可搬。
- **保持 delta/hash 语义**：`content_hash` 的增量跳过行为、`cache-update` 的原子写入与加锁语义不能被破坏。
- **单一来源**：改动集中在 `.skills/` 与少量 Python 模块，不触碰符号链接镜像。

---

## Terminology Baseline

| 类别 | 规范术语 | 含义 | 反例 |
| --- | --- | --- | --- |
| 路径 key | **vault-relative key** | 以 vault 根为基准的 POSIX 路径，无前导 `./`，不含 `..` | `/Users/name/Knowledge/Raw/x.pdf` |
| 路径 key | **home-relative key** | 以 `~`（或可展开环境变量）开头的路径，读取时展开 | `/Users/name/.claude/projects/x.jsonl` |
| 路径 key | **pseudo-key** | 带 `scheme:` 前缀的非文件标识符，如 `agent:`、`url:`、`repo:` | 裸相对路径 `../foo` |
| 元数据 | **source_hint** | 可选、仅供本机重新打开文件的位置提示，不是身份标识 | 用作 manifest 的 `sources` key |
| 元数据 | **source_repo** | 项目的可移植标识（git remote URL / 仓库 slug） | `source_cwd` 绝对路径 |
| 解析 | **resolve_key** | 把任意合法 key 归一为绝对路径（或确认为非文件 pseudo-key）的函数 | 直接字符串比较 |

---

## Goals

1. `.manifest.json` 的 `sources` key、页面 frontmatter 的 `sources:`、以及项目 `source_cwd` 中不再出现机器绝对路径；同一 vault 跨机器同步时这些字段的字符串完全一致。
2. `llm-wiki` 中存在唯一、可引用的契约定义；其余 skill 文档只引用不再复述，消除"文档教绝对路径"的源头。
3. 代码读取路径对旧绝对 key、新相对 key、`~` 相对 key、pseudo-key 四种形态都能正确匹配，不产生重复摄取或误报 missing。
4. 提供可 dry-run、可回滚的迁移路径，把既有 vault 的绝对 key 转为契约 v2 形态。
5. 存在自动化测试锁住契约，防止文档与代码再次漂移。

---

## Non-Goals

- **不**改变 `.manifest.json` 的整体 schema、`content_hash` 语义或 delta 算法本身。
- **不**要求把 T2（vault 外）源复制进 vault；也不新增源同步/搬运机制。
- **不**在本设计内统一 manifest 的字段命名漂移（`ingested_at` vs `last_ingested`、`pages_created` vs `pages_produced` 等）。该问题独立于路径议题，见 `Open Questions`。
- **不**修改符号链接镜像机制；`.skills/` 仍是唯一来源。
- **不**处理 OKF 导出/导入的 `resource` 字段；如相关，另行评估。

---

## Design

### 1) 契约 v2

以下条款构成契约正文，落点为 `llm-wiki/SKILL.md` 的 `.manifest.json` 章节。

- **C1 — 禁止裸绝对路径（新写入）。** 任何新的持久化写入（manifest `sources` key、页面 frontmatter `sources:`、项目 `source_cwd`）都不得存储机器绝对路径；旧 vault 中已存在的绝对 key 保持可读（见 `Compatibility and Invariants`）。唯一的例外是**无法可移植化**的源：当源既不在 vault 内、也不在 `$HOME` 下，且调用方未提供 pseudo-key 时，`update_source` 会退化为原样保存该路径，并**向 stderr 输出 `no portable key` 告警**（不静默）；`migrate` 对同类保留项也会告警。调用方应显式传 `--key` 消除该例外。
- **C2 — vault 内源用 vault-relative key。** POSIX 分隔符、无前导 `./`、不含 `..`，例如 `Raw/database/xxx.pdf`、`Clippings/xxx.md`。
- **C3 — `$HOME` 下的 vault 外源用 home-relative key。** 以 `~` 开头，例如 `~/.claude/projects/-Users-name-my-app/abc123.jsonl`。`~` 在不同机器上展开为各自的 home，因此字符串本身可移植。
- **C4 — 其余 vault 外源用 pseudo-key。** 不落在 `$HOME` 下的源（外部挂载、项目目录、网页）使用命名空间伪 key，禁止退化为绝对路径：git 项目用 `repo:<remote-url>`，网页用 `url:<canonical-url>`，会话日志用 `agent:<agent>/<id>`。
- **C5 — 比较前必须归一。** 任何 key 比较都先经 `resolve_key`：绝对路径直接使用；`~`/环境变量展开；pseudo-key 视为非文件标识符按原字符串比较；其余按 vault 根解析。禁止在归一之前做字符串相等比较。
- **C6 — `source_hint` 仅作提示。** 需要记录本机位置时使用可选 `source_hint`（`~` 相对），它不参与身份匹配、不得作为 `sources` key、缺失时不得导致错误。

### 2) key 形态决策表

| 源的位置 | 规范 key | 示例 | 是否文件路径 |
| --- | --- | --- | --- |
| vault 内 | vault-relative | `Raw/database/postgres.pdf` | 是 |
| `$HOME` 下 | home-relative | `~/.claude/projects/-Users-name-my-app/abc.jsonl` | 是 |
| git 项目（可在任意路径） | pseudo-key | `repo:github.com/Ar9av/obsidian-wiki` | 否 |
| 网页 | pseudo-key | `url:https://example.com/article` | 否 |
| agent 会话 | pseudo-key | `agent:claude/<session-id>` | 否 |
| 其它 vault 外且无稳定标识 | 显式 pseudo-key + 可选 hint | `src:<sha256-8>` + `source_hint: ~/docs/x.md` | 否 |

最后一行**不会自动推导**：`stored_key` 对该类路径返回 `None`，调用方必须经 `--key` 显式指定，否则退化为原样保存路径（见 C1 例外）。`src:<sha256-8>` 只是推荐的伪 key 命名，不是工具生成的值。

`cache.py` 现有 `_is_file_key` 已按 `://` 与 `scheme:` 前缀把 pseudo-key 排除在文件存在性检查之外（`obsidian_wiki/cache.py:199-202`），因此 C4 与既有实现兼容，`missing` 检测不会对 pseudo-key 误报。

### 3) 解析顺序

```
resolve_key(key, vault):
  ┌────────────────────────────────────────────────────────────┐
  │ 1. key 是 pseudo-key?  (含 "://" 或匹配 ^scheme:[^/\\])    │ → 返回 key（非文件身份）
  │ 2. key 以 "~" 开头或含可展开变量?                          │ → expanduser/expandvars → 绝对路径
  │ 3. key 是绝对路径?                                         │ → 直接使用
  │ 4. 否则                                                    │ → vault / key
  └────────────────────────────────────────────────────────────┘
```

该顺序是 C5 的可执行定义。第 4 条覆盖 C2，第 2 条覆盖 C3，第 1 条覆盖 C4；第 3 条是迁移过渡期对旧数据的兼容。

### 4) 写入侧归一

写入是杜绝绝对路径复现的关键。新增 `stored_key(path, vault)`，与 `resolve_key` 互逆：

- `path` 在 `vault` 内 → 返回 vault 相对路径；
- `path` 在 `$HOME` 下 → 返回 `~` 相对路径；
- 否则 → 返回 `None`（无可移植表示）。调用方应显式传 `key=`；未传时 `update_source` 退回保存原始路径以保持向后兼容，这是 C1 记录的唯一例外。

落点：

- `obsidian_wiki/cache.py` 的 `update_source` / `_update_source_locked`：新条目使用 `stored_key(source_path, vault)`；无显式 `key` 且 `stored_key` 为 `None` 时退回原始路径。`_same_source`、`_missing_on_disk` 增加 `~`/环境变量展开。新增可选 `key=` 参数：**显式 key 是权威的**，即使 manifest 中已有按路径匹配的旧条目，也会把该条目 re-key 到显式 key（读取侧仍双向兼容）。另有可选 `source_hint=` 参数，把 `~` 相对位置提示写进条目；它是 advisory 元数据、非身份标识，未传时保留既有值。
- `scripts/manifest.py`：新增 `resolve_key` / `stored_key` 供解析与迁移使用；`cmd_delta` 用 `resolve_key` 构建已知源映射，使 vault-relative / home-relative key 能被扫描到的绝对路径直接命中。
- `obsidian-wiki cache-update` CLI 透传调用方路径，并新增 `--key` 与 `--source-hint`：skill 可以继续传绝对路径（落盘被归一为相对形态），对无可移植路径的源则显式传伪 key，`src:<sha256-8>` 这类不自带位置的 key 可再配 `--source-hint ~/...`。这让文档改动与代码改动可以分批上线而不互相阻塞。

### 5) 读取侧兼容与降级

| 场景 | 行为 |
| --- | --- |
| manifest 中是旧绝对 key，调用方传绝对路径 | 直接命中（现状不变） |
| manifest 中是 vault-relative key，调用方传绝对路径 | `resolve_key` 归一后命中（现状已支持） |
| manifest 中是 home-relative key，调用方传绝对路径 | 展开 `~` 后命中（本设计新增） |
| pseudo-key | 不参与文件存在性检查；由内容 hash 或显式映射参与增量判断 |
| 本机不存在该源（跨机同步的另一台机器） | 不误报 `missing`：机器本地 key（home-relative / 绝对）缺文件时归入独立的 `unavailable` 桶；`missing` 只保留 vault 本地源的真实缺失。若 delta 扫描不到则视为"本机无此源"，不触发重摄取 |
| `cache-check` 输出 | 新增 `unavailable` 字段（机器本地但本机不存在）；`missing` 语义收窄为 vault 本地源缺失 |
| 遗留 ingest-root 相对 key（如 `-Users-x/abc.jsonl`） | 按原字符串保留，不报错、不改写；`_match_relative` basename 后缀兜底继续兼容 |
| 调用方显式传 `--key` | re-key 已按路径匹配的条目；读取侧对旧 key 仍兼容 |
| `manifest.py delta` 标准输出 | 仍输出绝对路径——这是临时输出，调用方用它打开文件，不是存盘 key |

### 6) 迁移与回滚

新增 `scripts/manifest.py migrate <vault> [--dry-run]`；`normalize` 保留为等价别名，旧指令不致失效：

- vault 内绝对 key → vault-relative；
- `$HOME` 下绝对 key → home-relative；
- 不在 vault 内也不在 `$HOME` 下的绝对 key → 保留为绝对路径并打印 `no portable form` 警告（不删除，避免丢失 provenance）；
- pseudo-key 与遗留 ingest-root 相对 key → 原样保留（后者无法在无文件的情况下与 vault-relative 区分，改写有损坏风险）；
- 合并因归一产生的碰撞条目，`_newest()` 保留最新 `ingested_at`，并合并 `pages_created` / `pages_updated` / `pages_produced` 三个列表。`pages_produced` 是本实现补上的：原 `_newest` 只并前两个字段，迁移碰撞会静默丢掉 cache 时代的 provenance。

兼容性保证：**读取始终兼容旧绝对 key**，故迁移是可选的性能/整洁优化而非正确性前提。回滚方式为 `git checkout -- .manifest.json`（迁移只改 manifest，不改页面内容；页面 frontmatter 由后续 skill 运行逐步收敛）。

---

## Compatibility and Invariants

以下行为在本次变更中保持不变：

1. `content_hash` 仍是增量跳过的首要信号；路径形态变化不改变跳过判定。
2. manifest 的两种 `sources` 形态（dict-keyed 与 list-of-objects）继续被透明读取；`update_source` 继续在保持形态的前提下原地更新 `obsidian_wiki/cache.py:7-20`。
3. `cache-update` 的 manifest 加锁与原子写不变（`manifest_lock`）。
4. 旧 vault 不迁移也能正确工作；当源可被表示为 vault-relative / home-relative，或调用方显式传了 `key=` 时，新写入不再产生绝对 key。无法可移植化的源是已知例外（见 C1）。
5. `pages_created` / `pages_produced` 自始即为 vault 相对路径（`.skills/llm-wiki/SKILL.md:157`），不在本次变更范围内。
6. 符号链接镜像与 `setup.sh install_skills` 行为不变。

---

## Incremental Plan

### Phase A：定义契约（低风险，先行）

- 重写 `.skills/llm-wiki/SKILL.md:146-158` 的 `.manifest.json` 章节为契约 v2 正文（C1–C6 + key 形态表）。
- 新增仓库根目录 `relative_paths.zh.md`（本文件）作为设计依据；是否移入 `docs/` 并登记索引留待完成后决定（见 Open Questions #3）。
- 不触碰代码，此阶段仅统一术语与规则。

### Phase B：代码归一与解析（核心）

- 在 `obsidian_wiki/cache.py` 增加 `resolve_key` / `stored_key`；改造 `_same_source`、`_missing_on_disk`、`update_source`；`cache-update` 增加 `--key`。
- 在 `scripts/manifest.py` 增加同源 helper，`cmd_delta` 改用 `resolve_key` 构建已知源映射；`_match_relative` basename 后缀兜底**保留**用于遗留 ingest-root 相对 key（实现调整，见 As-built Notes #1）。
- 保证读取路径先兼容旧的绝对 key，再叠加新形态；现有测试必须全绿（唯一豁免：`test_update_appends_new_list_entry` 的断言按新契约更新为 vault 相对 key）。

### Phase C：skill 文档收敛

- `wiki-status:32,39,48,60`：断言改为引用契约 v2；示例 key 改为相对/伪 key 形态；`source_path` 改为 `source_repo` + 可选 `source_cwd_hint`。
- `wiki-update:202,219`：`source_cwd` → `source_repo`（+ `source_cwd_hint`），log 模板同步。
- `wiki-ingest:462-476`：Step 7 增加"key 必须为 vault-relative / home-relative / pseudo-key"硬约束，并指向 `llm-wiki` 契约。
- `claude-history-ingest:38`：删除"keys are absolute paths"，改为引用契约；示例命令改用 `migrate`。
- `wiki-query:274-277,302`：以 `source_repo` 为权威标识，输出 repo URL；仅在 `source_cwd_hint` 解析出的本地 checkout 存在时才附带绝对路径作为补充。
- `wiki-rebuild:59`：`vault_path` 示例改用 `$OBSIDIAN_VAULT_PATH` 占位。

### Phase D：迁移与固化

- 交付 `manifest.py migrate`，在真实 vault 上 `--dry-run` 验证后执行。
- 新增文档契约测试与解析单测（见 Validation）。
- 视情况增加一个 lint 检查：`.skills/**/*.md` 中出现 `/Users/`、`/home/`、`/absolute/` 等模式即失败。

### Phase E：可选上游化

- 本契约与上游 `llm-wiki` 的绝对路径规定相反。若长期只在 fork 内维护，每次同步 upstream 都会在 `llm-wiki`、`wiki-status` 等文件产生冲突。建议将 Phase A 的契约文本作为独立 PR 提交上游；若不提交，则需在 fork 中记录冲突解决策略。

---

## Validation Strategy

- **解析单测**：`resolve_key` / `stored_key` 覆盖 vault 内、`$HOME` 下、pseudo-key、绝对路径四类输入，以及 `~` 与环境变量展开。
- **匹配单测**：`_same_source` 在"旧绝对 key vs 新绝对查询"、"vault-relative key vs 绝对查询"、"home-relative key vs 绝对查询"下均命中；pseudo-key 不触发 `missing`；跨机缺失的机器本地 key 归入 `unavailable` 而非 `missing`。
- **写入单测**：`update_source` 对 vault 内路径落盘为 vault-relative、对 `$HOME` 下路径落盘为 home-relative；对无可移植形式的源，显式 `key=` 落盘为该伪 key，未传 `key=` 时退回原路径**且 stderr 必须出现 `no portable key` 告警**；vault 内/`$HOME` 下/显式 `key=` 三种情形不得告警；`source_hint=` 会写入条目，未传时保留既有值；传入绝对路径 hint 时按 `stored_key` 同规则归一（`$HOME` 下 → `~/…`，vault 内 → vault 相对），使 shell 展开过的 hint 也不会写入机器绝对路径。由 `tests/test_portable_keys.py` 与 `tests/test_cache.py::TestCacheCLI` 覆盖。
- **迁移单测**：对混合形态的 fixture manifest 执行 `migrate --dry-run` 输出预期；执行后幂等（再跑一次 no-op）；碰撞条目按 `_newest` 合并且保留三个页面列表字段；`normalize` 别名等价；写入门槛按新旧映射是否相同判定，含 `..` 的非规范绝对 key 会被规范化并落盘，且不得打印 `already portable`。
- **一致性测试**：`tests/test_manifest_portable_keys.py::ResolveStoreParityTest` 对 `cache.py` 与 `manifest.py` 两份 `resolve_key` / `stored_key` 实现跑同一组输入矩阵，断言结果一致——两份实现因 `manifest.py` 需独立运行而无法共用代码，该测试是防止单边漂移的护栏。
- **文档契约测试**：新增 `tests/test_portable_path_docs.py`，断言 `llm-wiki` 契约段存在、相关 skill 引用契约，且非 `references/` 的 skill 文档中不出现 `"/absolute/"`、`"source_cwd":`、`source_cwd=`、`"path": "/`、`"source_path": "/`、`sources: ["/` 等存储型绝对路径字面量。`references/` 下的原始数据格式文档（如会话 `cwd` 字段）豁免。同时断言 `docs/cli.md` 记录了 `--key` 与 `manifest.py migrate`。
- **回归**：`tests/test_cache.py`、`tests/test_cache_manifest_shapes.py`、`tests/test_manifest_delta.py` 必须继续通过；新增用例不得改变既有跳过/修改判定。
- **端到端烟雾**：在含绝对 key 的旧 vault 上依次执行 `cache-check`（应正确识别 unchanged）→ `update_source`（应写入相对 key）→ `migrate`（可移植 key 应无残留绝对路径；不可移植的源按 C1 例外保留）。

---

## Risks and Mitigations

1. **home-relative 依赖 `$HOME` 布局** —— 两台机器 home 结构不同时，`~` 相对源仍可能失配。缓解：失配只降级为"本机无此源"，不触发错误写回；确需定位时用 `source_hint`。
2. **agent 历史目录名编码了原始绝对 cwd** —— `~/.claude/projects/-Users-name-my-app/` 中的目录名本身机器相关，`~` 相对也救不回。缓解：会话日志优先使用 `agent:` pseudo-key + 内容 hash 作为身份；接受跨机重新摄取作为已知降级。
3. **迁移期间的混合形态** —— 归一可能让两条旧 key 碰撞。缓解：`resolve_key` 读取兼容 + 迁移用 `_newest()` 合并，且迁移前强制 `--dry-run`。
4. **basename 后缀兜底的误匹配** —— 保留 `_match_relative` 后，basename 相同的遗留 ingest-root 相对 key 理论上可能误匹配。缓解：`resolve_key` 的确定性解析优先，兜底仅在解析不命中时作用于遗留形态；影响面限于非规范 key，且迁移可逐步消除。
5. **文档再次漂移** —— 缓解：契约测试在 CI 中失败即阻断。
6. **与上游持续冲突** —— 缓解：Phase E 上游化；否则将 fork 差异集中到少数段落，减少每次 rebase 的冲突面。
7. **pseudo-key 无法自动推导** —— 缓解：`stored_key` 对无法归类的 vault 外路径返回 `None`，调用方经 `--key` 显式指定；未指定时退回保存原路径并在 `migrate` 中告警，绝不静默丢弃条目。

---

## As-built Notes（实现与设计的五处调整）

落地实现与上面的原始设计有五处有意偏离，一律以生产安全为先；在此记录，避免文档与代码再次背离。

1. **保留 `_match_relative` basename 后缀兜底**（设计原计划删除）。真实 vault 里存在相对 ingest root 的遗留 key（如 `~/.claude/projects/` 下的 `-Users-x-github/abc123.jsonl`），删除兜底会让它们被误判为 NEW，且两个既有测试锁定了该容忍行为。现在兜底只在 `resolve_key` 不命中时作用于遗留形态。
2. **`manifest.py delta` 的标准输出保持绝对路径**（设计原计划改为 stored key 形态）。delta 的 stdout 是临时输出，调用方据此打开文件；契约约束的是持久化 key，不是 stdout。改动它只会降低调用方可用性，没有契约收益。
3. **无可移植形式的源退回保存原路径并告警，而非直接报错**（设计风险 #7 / 严格 C1）。硬报错会破坏外部挂载源的真实用法与既有测试。改为：`stored_key` 返回 `None` → 调用方应传 `--key` → 未传时保存原路径并**向 stderr 打印 `no portable key` 告警**（写入路径，`update_source` 与 `cache-update` CLI 均覆盖）；`migrate` 对同类保留项打印 `no portable form` 告警。C1 已把该例外写入条款。**修订**：初版仅在 `migrate` 告警、写入路径静默，评审指出该不一致后，写入路径已同步告警。
4. **显式 `--key` 是权威的，会 re-key 已匹配条目**（设计未明确）。冒烟测试发现：manifest 中已按路径跟踪的条目，仅传 `--key` 不会生效。现在显式 key 会把匹配条目 re-key 到该 key（若目标 key 已存在则合并）；派生 key 不做 re-key，避免意外重命名。
5. **`_newest` 额外合并 `pages_produced`**（设计未提）。原 `_newest` 只并 `pages_created` / `pages_updated`，而 cache 时代使用 `pages_produced`，迁移碰撞会静默丢失 provenance。现在三个列表字段都合并。

另有一处测试修正：`test_update_appends_new_list_entry` 的断言由绝对路径改为 vault 相对 key `_raw/foo.md`——那条断言正是旧契约的体现。

**评审后续修复。** 代码评审又发现三处，已一并落地：（a）`migrate` 的写入门槛改为按新旧映射是否相同判定，含 `..` 的非规范绝对 key 不再出现"未写盘却报 already portable"；（b）为 `cache.py` 与 `manifest.py` 的两份 key helper 增加一致性测试 `ResolveStoreParityTest`，两侧无法共用代码（`manifest.py` 需独立运行），故以测试护栏代替抽取共享模块；（c）`check_sources` 新增 `unavailable` 桶，跨机缺失的机器本地 key 不再混入 `missing`，兑现 §5 "不误报 missing" 的承诺，`missing` 收窄为 vault 本地源的真实缺失。另有一处文档补漏（d）：`docs/cli.md` 之前漏写第四种伪 key 命名空间 `src:<sha256-8>`，已补齐，并将文档契约测试加强为强制 `repo:`/`url:`/`agent:`/`src:` 四个命名空间同时出现在 `docs/cli.md` 与 `llm-wiki` 契约中。后续又发现（e）：文档描述了 `source_hint`，但 CLI 与库都没有写入途径；选择补齐能力而非弱化文档——新增 `source_hint=` 形参与 `cache-update --source-hint`，使该 advisory 字段可由并行路径写入，未传时保留既有值。紧接的（f）：`source_hint` 若不做归一，文档里未加引号的 `--source-hint ~/docs/x` 会被 shell 先展开成绝对路径落盘；新增 `normalize_hint`，绝对 hint 按 `stored_key` 同规则转回 `~/…` / vault 相对，并把文档示例改为加引号。再一处（g）：CLI 回执曾回显归一前的原始入参，与 manifest 实际存值不一致；`cache-update` 现改为回显 `normalize_hint` 之后的值，与同一 JSON 中 `key` 字段的归一语义对齐。

---

## Alternatives Considered

**A. 保留绝对 key，靠每台机器各自迁移修复。** 否决：违反 vault 契约，且只要 skill 文档不变，每次按文档操作都会重新引入；不可持续。

**B. 要求所有源先复制进 vault，统一用 vault-relative。** 否决：改变摄取拓扑，造成数据重复；agent 历史与外部项目无法经济地复制；与 `llm-wiki` "sources live wherever the user keeps them" 的既有模型冲突。

**C. 全部 vault 外源一律用 pseudo-key（不用 home-relative）。** 部分否决：对 `$HOME` 下、跨机布局一致的常见源（agent 历史缓存、个人文档），home-relative 更直观、保留了可重新打开文件的能力；强制 pseudo-key 会丢失这一能力并需要额外的 hash 身份映射。折中采用 C3 + C4 的混合。

**D. 只改文档、不动 `manifest.py`。** 否决：`manifest.py` 的 `canonical()` 仍无条件绝对化，`cmd_delta` 也按绝对路径构建已知源映射，写入侧会继续产生绝对 key，文档与实现持续矛盾，问题会在下一次 ingest 复现。

---

## Open Questions

以下为已明确**有意延后**、不阻塞本设计的条目：

1. **manifest 字段命名漂移**：`wiki-status` 示例使用 `ingested_at`/`modified_at`/`pages_created`，而 `wiki-ingest` Step 7 与 `cache.py` 实际使用 `last_ingested`/`pages_produced`。与路径议题正交，建议单独立项统一，避免本次改动范围膨胀。
2. **是否上游化 Phase A 契约**：决定长期冲突成本与维护方式；可在实现完成后评估，不影响 Phase A–D 的本地正确性。
3. **本设计文档的最终归属**：当前放在仓库根目录；若纳入发布文档体系，需决定是移入 `docs/` 并登记 `docs/README.md` 索引，还是建立独立的 `docs/proposals/` 区域。
