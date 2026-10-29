# Hanluwork 本地测试报告

日期：2026-10-29。由 worker1、worker2 实现及测试，master 审查并执行最终检查。

## 自动化测试

命令：`.venv/bin/python -m pytest -q`

结果：39 passed，8 subtests passed。有一条 Starlette TestClient 对 httpx 的弃用提醒，当前测试不受影响。

覆盖 MCP 初始化、工具 schema 和只读注解、时间校验、正常及空结果、数量截断、Host 防护、真实模式拒绝、错误脱敏、Gmail 分页和调用上限、MIME/HTML 正文解析、附件跳过、正文完整性，以及本地 OAuth 的 scope、用户拒绝、输出权限和已有文件保护。

## Inspector 实测

版本：2.13.0；本机使用 bundled Node.js 24.19.0 与 pnpm dlx 运行同一官方 Inspector。连接实际监听的 `http://127.0.0.1:8000/mcp`，不是直接调用 Python 函数。

| 用例 | 实际结果 |
| --- | --- |
| tools/list | 发现 list_emails，参数与只读注解正确 |
| 2026-10-22 至 2026-10-29，limit=50 | 返回 3 封合成邮件，is_mock=true |
| 同一范围 limit=1 | 返回 1 封，has_more=true、coverage_complete=false |
| 2020-01-01 至 2020-01-02 | 返回空数组，count=0 |
| start=invalid | 返回 isError=true；Inspector 退出码 5（预期错误） |

## 尚未验证

- 真实 Gmail OAuth 和邮件 API；测试仅使用模拟客户端，未访问真实邮箱。
- Cloud Run 容器构建与云端部署。
- Gemini 工具选择、总结、分类和追问。
- Gemini→MCP 的云端 OAuth、用户身份绑定及令牌存储。

当前可交付的是本地模拟原型；不能称为真实邮箱端到端功能已完成。
