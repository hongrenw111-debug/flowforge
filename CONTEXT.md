# styleforge

驱动 Google Flow 网页端、把一串镜头按剧本链式生成连续视频的自动化工作台。

## Language

**剧本 (Script)**:
一次连续生成任务的完整定义：有序的镜头列表加全局设置。
_Avoid_: 工作流、任务、Flow 项目

**镜头 (Shot)**:
剧本中的一个生成单元：一段提示词加一个首帧来源，产出一 SV。与之相对，Flow 页面上的「场景/scene」指它自己的画布概念，勿混用。
_Avoid_: 场景、片段、clip

**首帧 (First Frame)**:
一段镜头生成时提供的起始画面。
_Avoid_: 起始帧、start frame

**尾帧 (Last Frame)**:
一段视频的最后一帧画面，可被下一镜头用作首帧。
_Avoid_: 结束帧、末帧

**首帧来源 (First-Frame Source)**:
镜头首帧的三种取值：指定图片、上一镜头尾帧、空（纯文生视频）。

**链式续接 (Chaining)**:
把上一镜头的尾帧自动作为下一镜头首帧的机制。

**断点续跑 (Resume)**:
重跑剧本时跳过已成功镜头、从失败处继续的能力。
_Avoid_: 续传

**驱动层 (Driver)**:
可替换的浏览器操作实现；v1 为 bb-browser，预留 Playwright 实现。
_Avoid_: 引擎、backend

**页面定位 (Locator)**:
对 Flow 页面元素的定位描述，集中管理以应对网页改版。
_Aavoid_: selector、选择器
