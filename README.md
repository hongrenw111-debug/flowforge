# FlowForge

[![CI](https://github.com/hongrenw111-debug/flowforge/actions/workflows/ci.yml/badge.svg)](https://github.com/hongrenw111-debug/flowforge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)

> 🤖 **Agent-Native AI Director for Google Flow**  
> **面向 AI 智能体（LLM / Coding Agents）的声明式短剧与连续视频自动生成工作台**  
> 零 API Key 依赖 · 真实浏览器 CDP 驱动 · 锚定帧/尾帧接力双模式 · 物理级伪视频拦截与 MAD 质检双闸

---

## 💡 为什么需要 FlowForge？

在生成式 AI 时代，大语言模型（如 Claude、GPT、Gemini、DeepSeek 等 Coding Agents）擅长编写剧本与调度分镜，但在驱动视频网页端（如 Google Flow / Omni 1.1 Flash）时常常面临痛点：
1. **官方通常没有公开 API** 或门槛极高；
2. **多镜头画风漂移**，角色发型、服装无法连续；
3. **网页富文本编辑器（如 Slate.js）难以自动化操控**，普通脚本无法触发 React 状态。

`FlowForge` 专为 **AI 智能体直接操纵** 设计。任何接入 Terminal / Bash 工具的 AI 智能体，只要一条命令即可像人类专业导演一样全自动完成多镜头视频的生成、下载与品控。

```
                    ┌─────────────────────────┐
                    │    AI Agent (你/LLM)     │
                    │  (Claude, GPT, Gemini)  │
                    └────────────┬────────────┘
                                 │ 1. 输出声明式剧本 (YAML)
                                 ▼
                    ┌─────────────────────────┐
                    │        FlowForge        │
                    │  (编排引擎 + 质检双闸)   │
                    └────────────┬────────────┘
                                 │ 2. CDP 拟人化无障碍控制 (bb-browser)
                                 ▼
                    ┌─────────────────────────┐
                    │   用户本地 Chrome 浏览器  │
                    │ (Google Flow 真实已登录会话)│
                    └────────────┬────────────┘
                                 │ 3. 产出高清视频 (mp4) + 首尾帧抽检
                                 ▼
                    ┌─────────────────────────┐
                    │ 完整成片 & MAD 质检报告  │
                    └─────────────────────────┘
```

---

## 🌟 核心特性

### 1. 🛡️ 零 API Key · 极致隐私保护
- **无凭据泄露风险**：完全无需在代码或环境中配置 Google API Key、Token 或账密。
- **本地会话复用**：通过底层无障碍树与 CDP 协议直接复用创作者本地 Chrome 已登录的 Flow 状态，数据零上传第三方服务器。
- **内置脱敏机制**：所有日志与异常信息严格过滤 Cookie、Bearer Token 与邮箱地址，符合工业级隐私安全红线。

### 2. 🎬 两种生成模式（Agent 自主决策）
| 模式 | 核心机制 | 最佳应用场景 | 质量监控 |
|---|---|---|---|
| **锚定帧模式 (Anchor Frame)** | 每镜均使用同一张高精角色原画 CG 作为输入首帧 | 快速切镜、对话分镜、**画风与角色绝对锁定** | 首帧保真度 MAD 质检 |
| **尾帧接力模式 (Last-Frame Relay)** | 自动提取上一镜生成的第 8 秒最后一帧作为本镜首帧 | 连续动作跟拍、长镜头叙事、**动作自然衔接** | 逐跳累积漂移分析 (`flowforge drift-test`) |

### 3. ⚖️ 工业级双闸防护（防幽灵产物与伪视频）
- **物理完整性校验**：自动拦截网络异常导致的 14 字节假文件（如 `No session found` 错误响应），通过 `ffprobe` 严格校验 moov atom 与真实视频时长。
- **逐镜 MAD 量化质检**：自动抽取每镜首尾帧并计算 $64\times36$ 全像素平均绝对差（MAD），超阈值自动标记 `suspect` 警告供 AI 审阅。

---

## 🚀 智能体与开发者上手指南

### 1. 安装
```bash
# 克隆仓库并安装
git clone https://github.com/hongrenw111-debug/flowforge.git
cd flowforge
pip install -e .
```

### 2. 让 AI 生成并校验剧本 (`story.yaml`)
```yaml
name: sakura-story-v1
defaults:
  model: omni-1.1-flash
  duration: 8
  aspect: "16:9"
  outputs: 1
  download: original-720p
  retry: 0
  mad_threshold: 25.0
shots:
  - prompt: "第一镜：阳光洒在车站月台上，金发少女回头微笑。(Audio: '今天天气真好呢。')"
    first_frame:
      source: image
      path: assets/character.png
  - prompt: "第二镜：微风吹过月台，少女转身迈向车门。(Audio: '列车要进站了。')"
    first_frame:
      source: last_frame  # 尾帧自动接力
```

AI 校验剧本语法：
```bash
flowforge check story.yaml
```

### 3. AI 自动调度执行
```bash
# 离线模拟演练（零点数消耗，验证编排与文件流转逻辑）
flowforge run story.yaml --fake

# 真实真机生成（驱动浏览器执行，零自动重试安全闸门）
flowforge run story.yaml --yes

# 单镜冒烟测试与接力漂移实验
flowforge smoke --fake
flowforge drift-test --shots 3 --fake
```

---

## 📝 提示词规范（推荐 AI 遵守）

1. **视觉特征重复锁定**：每镜即使挂了首帧，仍需在提示词中重复发色、瞳色、服装材质与年代风格。
2. **安全合规替代词**：使用客观中性的视觉描绘（如“暗红光影”、“剧烈震颤”），规避风控词。
3. **Omni 原生音频台词语法**：
   - 角色台词使用英文双引号：`"出发吧！"`；
   - 环境音与配乐使用小括号：`(Audio: soft wind blowing, cinematic strings)`。

---

## 🔒 免责声明

1. **个人自用与研究定位**：`FlowForge` 为开源的效率工具与自动化调度框架，与 Google 或 Google Flow 官方无任何隶属关系。
2. **合规使用**：使用者需遵守 Google 服务条款（ToS）。本项目不提供任何绕过风控或账号限制的能力。
3. **积分透明**：真实模式默认开启授权闸门并实行零自动重试策略，点数消耗完全透明由用户掌控。

---

## 📄 许可证

基于 [MIT License](LICENSE) 开源发布。
