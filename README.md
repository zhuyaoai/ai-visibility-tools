# ai-visibility-tools

检测"AI 在回答问题时有没有提到你、有没有引用你的内容"的只读工具集。

## 三层判定

做 AI 可见度监测前必须先分清这三件事，否则会把"收录"当成"引用"：

| 层 | 含义 | 判据 |
|---|---|---|
| **L1 收录** | 内容进了 AI 的检索库 | 回答的「参考资料」来源列表里出现你的页面 URL |
| **L2 引用** | AI 回答时真正用了你的内容 | 正文确实转述了你的观点（不是只挂来源） |
| **L3 带品牌** | 引用时带出你的品牌/账号名 | 回答正文或来源标注里出现品牌词 |

⚠️ **最大陷阱**：一般性技术问题 AI 用自己的知识也能答对。测试必须用**只有你能提供的事实**（具体字段名、阈值、实测数字），泛问"怎么判断内容质量"测不出真引用。

## 工具

### 1. doubao-read —— 豆包会话只读读取

读取豆包已登录会话的回答正文与「参考资料」来源列表。**只读，不发送任何消息。**

```bash
node doubao-read/doubao_read.mjs --list   # 列出会话（拿 id）
node doubao-read/doubao_read.mjs          # 按 targets.json 全量读取
node doubao-read/doubao_read.mjs --only A1,A3
```

前置：本地 Edge 开 CDP（`--remote-debugging-port=9222`）、豆包已登录、依赖 playwright-core。

### 2. platform-check —— 平台可抓取性与收录巡检

检查：robots.txt 是否放行 AI 爬虫（GPTBot / ClaudeBot / PerplexityBot 等）、文章是否进入平台 sitemap、账号名是否在**服务端返回的 HTML** 中。

```bash
python platform-check/check_platforms.py
python platform-check/check_platforms.py --only-author
```

## 已知坑（实战踩过）

1. **侧边栏假阳性**：聊天页侧边栏的会话标题含被检测关键词，全文检索会误判命中——读取前必须剔除侧边栏文本
2. **渲染等待**：SPA 页面需轮询等正文长度稳定（连续两次不变且 >800 字符）再读
3. **豆包对无 JS 通道返回壳页**：必须走本机 CDP，普通 HTTP 抓取拿不到内容
4. **限流误报**：高频请求返回的是验证页，若不识别会得出"账号名不可读"的相反结论——异常必须报 ERROR，不能直接判定
5. **判据对象要匹配**：用"缺 title/doctype"判断页面完整性会误杀 XML（sitemap 天生没有 title）

## License

MIT © 逐曜AI
