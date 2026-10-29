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

- （本地只读抽样已完成，见下文）真实 Gmail 云端端到端集成仍待完成。
- （已完成，见下文）Cloud Run 容器构建与云端部署。
- （基础分类总结已完成，见下文）Gemini 自由追问等更多场景仍待验证。
- Gemini→MCP 的云端 OAuth、用户身份绑定及令牌存储。

当前已有云端模拟流程及本地真实 Gmail 抽样读取；真实邮箱的 Gemini 端到端功能尚未完成。

## Cloud Run 验证（2026-10-29）

- 已部署代码：`4e2def61826a568079fbf25ae204b1614980ebad`。
- 项目：`hackathon-otpj`，服务：`hanluwork`，区域：`us-west1`。
- Revision：`hanluwork-00003-gpk`，构建日志确认承接 100% 流量。
- MCP endpoint：`https://hanluwork-767668793384.us-west1.run.app/mcp`。
- `/health` 实测返回 status=ok、app=Hanluwork、mode=mock。
- Inspector 2.13.0 对云端 list_emails 调用成功，limit=1 返回一封模拟邮件并准确提示结果截断。
- Cloud Build 已连接 GitHub developer 分支，后续推送会自动构建部署，文档修改也可能触发构建。
- 用户本次测试预算为 10 美元；0–1 实例限制不是费用硬上限，尚未核实账单实际金额。

## Gemini 端到端验证

Gemini 开发者模式成功连接 Hanluwork，发现 list_emails 工具。实际提交固定日期范围的中文请求后，Event Log 显示 list_emails 返回 200 OK，耗时 553ms。回答正确列出 3 封模拟邮件，分类为工作、生活、其他，提取原文中的两项待跟进事项，并明确声明模拟数据而非真实邮箱。

对话地址：https://gemini.google.com/devmode/762941f97579bb28
本次未执行真实 Gmail 授权或读取；不将模拟验证视为真实邮箱功能完成。

## 本地真实 Gmail 验证（2026-10-29）

用户完成 Google 只读授权后，运行 `scripts/test_gmail_readonly.py` 成功（退出码 0）。查询最近 7 天，最多读取 5 封真实邮件，返回 count=5、body_content_complete=true。has_more=true、truncated=true、coverage_complete=false，表示仅验证抽样，不能据此声称已覆盖整周邮件。测试未显示邮件内容。

OAuth 配置和令牌仅保存在本机并被 Git 忽略，令牌文件权限为 0600。此次没有将真实凭据部署到 Cloud Run，Gemini 连接的服务仍返回模拟数据。后续需要实现 Gemini→MCP 身份验证、用户绑定及安全令牌存储，才能验证真实邮件的云端总结与分类。

## 云端 OAuth 实现验证（2026-10-29，部署前）

新增独立 Hanluwork OAuth issuer、Google Web OAuth 用户绑定、Firestore 加密存储和每用户 Gmail 读取。全套 pytest：102 passed、8 subtests passed；只有现有 Starlette TestClient 弃用提示。覆盖未授权401、scope不足403、错误resource、用户隔离、PKCE、授权码重放/并发兑换、Google nonce/issuer/email验证、confidential client限制、刷新scope、撤销与并发、凭据条件更新和错误脱敏。

MCP Inspector 2.13.0 对更新后本地 8001 端口执行 tools/list 成功，工具声明为 authorized email data，参数和只读注解正确。尚未完成此次版本的云端真实 Gmail 授权与 Gemini 调用；不会用 mock 测试替代。
