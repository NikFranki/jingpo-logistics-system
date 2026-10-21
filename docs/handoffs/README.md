# Session 交接记录

每个 Codex session 对应 `sessions/<session-id>.md`，避免不同 session 覆盖同一条记录。模板与整理规则分别见 [TEMPLATE.md](TEMPLATE.md) 和 [PROMPT.md](PROMPT.md)。

## 生成

导出当前 session 的对话为 UTF-8 Markdown 或纯文本，然后在仓库根目录运行：

```bash
make handoff SESSION_ID=<session-id> INPUT=/本地路径/session-export.md
```

也可以直接运行 `python3 scripts/handoff.py --session-id <session-id> --input /本地路径/session-export.md`。脚本把输入通过标准输入交给 `codex exec`，将生成结果写入对应的 session 文档。Codex 在只读沙箱中运行，执行使用当前 CLI 账号和默认模型；不会自动读取其他 Codex 会话。输入文件由操作者显式提供，不会复制进仓库；不要导出密钥、Cookie、Bearer 或无关个人信息。

脚本只在 Codex 成功且输出符合模板后原子替换目标 session 文件。失败时保留已有交接文档。生成后应 review 内容，尤其是完成状态、测试证据、风险和改动归属。

## 记录边界

- 仓库状态里的未提交改动可能来自其他 Agent，脚本会让 Codex 不要据此认领成果。
- 会话导出格式由操作者负责；Codex transcript 的内部文件格式不作为脚本接口。
- `sessions/` 下记录是交接材料，不自动代表产品验收或项目当前总状态。
- 不自动生成共享总索引；需要汇总时，单独核对各 session 文档后更新项目进度来源。
