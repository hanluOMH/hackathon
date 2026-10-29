# Hanluwork

Hanluwork 是一个计划接入 Gemini 的应用，由我们自己的 MCP server 提供支持。用户可以在 Gemini 中通过自然语言了解和整理自己的邮件。

## 首个使用场景

用户可以说：“@Hanluwork，帮我总结过去一周的邮件，并按工作、生活和其他分类。”

在用户明确授权后，应用读取邮件，由 Gemini 生成简洁、可追溯的总结。总结应突出重要邮件和可能需要跟进的事项，标明不确定的分类，并在可以提供时附上原邮件链接。用户也可以指定其他时间范围或继续提问。

## 第一版范围

- 在用户明确授权后，以 Gmail 支持的只读授权方式连接真实邮箱。
- 读取指定时间范围内的邮件，向 Gemini 提供总结和分类所需的信息。
- 使用远程 MCP server 连接 Gemini 和邮箱服务。
- 第一版专注于读取和总结，不发送、删除或修改邮件。
- 访问真实邮箱前必须征求许可；此前的讨论不代表已经获得访问许可。

邮箱服务已确定为 Gmail，部署平台为 Google Cloud Run；已选用 Google Cloud 项目 `hackathon-otpj`。当前已实现本地模拟 MCP 服务、可注入客户端的 Gmail 只读 adapter 和本地授权脚本；真实账号绑定、云端 OAuth、部署与 Gemini 联调尚未完成。

开发流程、分工与测试方法见 [开发文档](DEVELOPMENT.md)。

## 本地运行

需要 Python 3.12+；Inspector 需要 Node.js 22.19+。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python server.py
```

服务地址：`http://127.0.0.1:8000/mcp`；健康检查：`http://127.0.0.1:8000/health`。

在另一个终端启动 Inspector，选择 Streamable HTTP 并连接上面的地址：

```bash
npx @modelcontextprotocol/inspector@2.13.0
```

也可直接用 Inspector CLI 验证：

```bash
npx @modelcontextprotocol/inspector@2.13.0 --cli http://127.0.0.1:8000/mcp --transport http --method tools/list
npx @modelcontextprotocol/inspector@2.13.0 --cli http://127.0.0.1:8000/mcp --transport http --method tools/call --tool-name list_emails --tool-args-json '{"start":"2026-10-22T00:00:00-07:00","end":"2026-10-29T00:00:00-07:00","limit":50}'
```

测试样本是固定日期的合成邮件，上述范围返回 3 封。其他日期可能没有结果。当前默认且仅支持 `HANLUWORK_MODE=mock`，不能通过修改环境变量直接开启真实邮箱。

## 准备 Gmail 本地授权

在 Google Cloud 中配置 Gmail API、OAuth 同意页面和 Desktop app 客户端，将客户端文件保存在仓库之外，或以 `credentials*.json` 命名以符合忽略规则。用户在 Google 页面确认授权后，下列脚本将只读凭据保存在被忽略的 `token.gmail.json`，不读取邮件：

```bash
.venv/bin/python scripts/authorize_gmail.py --client-secrets /absolute/path/to/credentials.json
```

此脚本仅用于本地授权验证，不能代替云端应用的用户身份验证和 Gemini→MCP OAuth。不要把本地 Gmail token 放入公开的模拟 MCP 服务。

测试证据见 [TEST_REPORT.md](TEST_REPORT.md)。
