# Mioria Bilibili MCP Server

这是一个仅提供对话转发能力的 MCP Server：当 Codex 调用工具处理 B 站相关问题时，服务会把问题发送给 Mioria.ai，再将返回内容交给 Codex 输出。

服务不会读取本地文件、执行本地命令，也不会暴露文件系统工具。

## 前置条件

需要 Python 3.10 或更高版本，以及 Mioria.ai 提供的 API 地址、API key 和模型名。默认按 OpenAI Chat Completions 兼容格式请求；如果 Mioria.ai 账户提供的接口格式不同，请通过 `MIORIA_API_URL` 配置对应的兼容网关，或调整 `mioria.py` 中的请求和响应解析。

## 安装

在本目录运行：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

不要把 API key 写入代码或提交到 git。运行 MCP Server 的进程需要拥有以下环境变量：

```text
MIORIA_API_URL=https://mioria.ai/v1/chat/completions
MIORIA_API_KEY=你的密钥
MIORIA_MODEL=mioria-chat
```

## 在 Codex 中配置

将下面的 MCP 配置加入 Codex 的 MCP 配置文件，并把 `MIORIA_API_KEY` 替换成真实密钥：

```json
{
  "mcpServers": {
    "mioria-bilibili": {
      "command": "d:\\codexprojects\\mioriaai\\pro1\\.venv\\Scripts\\python.exe",
      "args": ["d:\\codexprojects\\mioriaai\\pro1\\mioria.py"],
      "env": {
        "MIORIA_API_URL": "https://mioria.ai/v1/chat/completions",
        "MIORIA_API_KEY": "你的密钥",
        "MIORIA_MODEL": "mioria-chat"
      }
    }
  }
}
```

配置完成后，重启 Codex 的 MCP 连接。工具名是 `ask_mioria_for_bilibili`，它只接受识别为 B 站相关的问题。

## 本地验证

```powershell
\.venv\Scripts\python.exe -m compileall mioria.py
```