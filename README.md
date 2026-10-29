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

邮箱服务已确定为 Gmail，部署平台为 Google Cloud Run；已选用 Google Cloud 项目 `hackathon-otpj`。当前已实现本地模拟 MCP 服务、可注入客户端的 Gmail 只读 adapter 和本地授权脚本；模拟版本已部署到 Cloud Run，并通过 Gemini 开发者模式端到端验证；真实 Gmail 账号绑定与云端 OAuth 尚未完成。

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

测试样本是固定日期的合成邮件，上述范围返回 3 封。其他日期可能没有结果。默认 `HANLUWORK_MODE=mock`。真实模式需要完整云端 OAuth 配置，缺少配置时启动失败，不会默认读取本机凭据。

## 准备 Gmail 本地授权

在 Google Cloud 中配置 Gmail API、OAuth 同意页面和 Desktop app 客户端，将客户端文件保存在仓库之外，或以 `credentials*.json` 命名以符合忽略规则。用户在 Google 页面确认授权后，下列脚本将只读凭据保存在被忽略的 `token.gmail.json`，不读取邮件：

```bash
.venv/bin/python scripts/authorize_gmail.py --client-secrets /absolute/path/to/credentials.json
```

此脚本仅用于本地授权验证，不能代替云端应用的用户身份验证和 Gemini→MCP OAuth。不要把本地 Gmail token 放入公开的模拟 MCP 服务。

测试证据见 [TEST_REPORT.md](TEST_REPORT.md)。

## 云端真实 Gmail 授权

`HANLUWORK_MODE=gmail` 启用独立的 Hanluwork OAuth issuer。Gemini 使用 Hanluwork 的访问令牌；Google Gmail 凭据保存在服务端，绝不作为 MCP bearer 返回。用户在 Google 同意页和 Hanluwork 同意页完成绑定后，工具按已认证的 subject 查询邮箱。

必需环境变量：`PUBLIC_BASE_URL`、`GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`OAUTH_ENCRYPTION_KEY`、`GMAIL_ALLOWED_EMAILS`、`GOOGLE_CLOUD_PROJECT`、`MCP_ALLOWED_HOSTS`。其中 client secret 与 Fernet key 从 Secret Manager 注入。Firestore 使用命名数据库 `hanluwork`，集合 `hanluwork_oauth`，授权记录加密保存。

Google Web OAuth 回调为 `PUBLIC_BASE_URL/oauth/google/callback`；Gemini OAuth scope 为 `gmail.read`，discovery 为 `PUBLIC_BASE_URL/.well-known/oauth-authorization-server`。动态注册仅允许 Google 官方 Gemini 回调和 confidential client；可用 `OAUTH_REDIRECT_ALLOWLIST` 进一步限定精确回调。真实 Gmail scope 只有只读权限，另含 `openid` 与邮箱身份权限用于绑定用户。

本机 Desktop OAuth token 不部署到 Cloud Run。真实云端验收必须在部署后另行记录，代码测试通过不能替代实际授权和工具调用。

## 云端模拟演示

- MCP 地址：`https://hanluwork-767668793384.us-west1.run.app/mcp`
- Gemini 应用名称：**Hanluwork**，已在开发者模式连接。
- 演示对话：https://gemini.google.com/devmode/762941f97579bb28
- 当前运行提交：`4e2def6`，只使用模拟邮件，不访问真实 Gmail。
- Cloud Build 已配置为跟踪 `developer` 分支；后续推送会自动构建和部署。
