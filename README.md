# FlowForge

> **声明式剧本驱动的 Google Flow 视频生成与质量把关工作台**  
> 支持「锚定帧画风锁定」与「尾帧接力动作连续」双模式，内置完整性校验与 MAD 首尾帧画质双闸。

---

## 🌟 核心理念与双模式诚实对比

在基于生成式视频模型（如 Google Flow / Omni 1.1 Flash）进行多镜头短剧或故事创作时，核心痛点是**多镜头间的画风与角色一致性**。`FlowForge` 提供两种并存的生产模式，不预设结论，由创作者根据实际题材与数据决定：

| 维度 | 锚定帧模式（Anchor Frame） | 尾帧接力模式（Last-Frame Relay） |
|---|---|---|
| **核心机制** | 每一镜均使用同一张高精度设计图/原画 CG 作为输入首帧 | 每一镜将上一镜生成的视频最后一帧提取为本镜输入首帧 |
| **画面表现** | **画风与角色绝对锁定**，多镜头无风格漂移，适合快速切镜与分镜叙事 | **镜头运动自然衔接**，适合连续长镜头与动作跟拍 |
| **潜在风险** | 镜间切换需要剧本提示词具有良好的场景调度 | 逐跳重渲染存在误差累积效应，多跳后可能发生画风漂移 |
| **自测工具** | `flowforge smoke`（单镜端到端验证） | `flowforge drift-test`（逐跳 MAD 漂移量化分析） |

> 💡 **项目取证与双闸机制：**  
> 在过往自动化实践中，我们曾发现网络异常导致的 14 字节假视频（如 `No session found` 响应页）被误存为 `.mp4`，导致接力链条物理断裂与误判。因此 `FlowForge` 内置了：
> 1. **体积下限 + ffprobe 真实时长与 moov atom 完整性校验**（彻底拒收伪视频）；
> 2. **逐镜首尾帧抽帧与 MAD（平均绝对差）量化监控**（超阈值自动标记 `suspect` 警告）。

---

## 🚀 三分钟上手

### 1. 环境准备
- **Python 3.10+**（Windows 用户推荐 `py`）
- **ffmpeg & ffprobe**（已加入系统 PATH）
- **bb-browser**（用于控制 Chrome，推荐 `npm install -g bb-browser`）

```bash
# 克隆仓库并安装开发依赖
cd flowforge
pip install -e ".[dev]"
```

### 2. 编写或校验剧本
创建 `my-story.yaml`：

```yaml
name: my-first-story
defaults:
  model: omni-1.1-flash
  duration: 8
  aspect: "16:9"
  outputs: 1
  download: original-720p
  retry: 0
  mad_threshold: 25.0
shots:
  - prompt: "第一镜：阳光洒在车站月台上，女主角回头微笑。(Audio: '今天天气真好呢。')"
    first_frame:
      source: image
      path: character-artoria.png
  - prompt: "第二镜：微风吹过月台，女主角转身迈向车门。(Audio: '列车要进站了。')"
    first_frame:
      source: last_frame  # 尾帧接力模式
```

校验剧本语法与参数合法性：
```bash
flowforge check my-story.yaml
```

### 3. 离线演练（零点数消耗）
使用内存假驱动（`--fake`）模拟全流程，验证文件流转与断点机制：
```bash
flowforge run my-story.yaml --fake
```

### 4. 冒烟测试与接力漂移实验
```bash
# 离线模拟单镜冒烟
flowforge smoke --fake

# 真实真机冒烟（需显式 --yes 授权，消耗真实 Flow 积分）
flowforge smoke --yes

# 尾帧接力漂移实验（2-3 跳量化评测，输出单跳与累积 MAD 报告）
flowforge drift-test --shots 3 --fake
```

---

## 📝 提示词编写规范

1. **角色特征每镜锁定**：即使提供了首帧图片，提示词中依然需重复角色的核心视觉特征（发色、瞳色、服装材质与年代风格）。
2. **安全过滤替代词**：避免出现敏感、政治或高风险冲突词汇，使用客观视觉描绘（如“暗红光影”、“剧烈碰撞”）。
3. **Omni 原生音频台词语法**：
   - 角色台词直接使用英文双引号标注：`"出发吧！"`；
   - 场景音效与情绪指令使用小括号语法：`(Audio: soft wind blowing, cinematic strings)`。

---

## 🔒 免责声明与合规说明

1. **非官方工具声明**：`FlowForge` 为开源的个人研究与效率辅助工具，与 Google 或 Google Flow 官方无任何隶属或背书关系。
2. **使用风险提示**：使用者需遵守 Google 服务条款（ToS）及相关使用政策。本项目不具备、亦不提供任何绕过安全风控或限制的能力。
3. **点数消耗说明**：真实运行模式下会驱动真实浏览器提交生成请求并消耗账户积分；真实模式默认开启授权闸门并实行零自动重试策略，请用户自行对点数消耗负责。

---

## 📄 开源许可

本项目基于 [MIT License](LICENSE) 开源发布。
