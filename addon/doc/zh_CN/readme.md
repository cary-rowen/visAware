# Vis Aware

Vis Aware 是一个用于 OCR 和图像描述的 NVDA 插件。它可以从导航对象、剪贴板、前台窗口或整个屏幕获取图像，并通过 NVDA 呈现识别结果。

插件仍在积极开发中，界面、设置和引擎支持可能会随版本变化。

## 主要功能

* 同时提供 OCR 和图像描述引擎。
* 支持的识别来源：
  * 导航对象
  * 剪贴板中的图像或图像文件
  * 前台窗口
  * 整个屏幕
* 结果输出方式：
  * NVDA 虚拟文档
  * 可浏览消息
  * 复制到剪贴板
  * 自动朗读虚拟文档结果
* 可通过输入手势切换引擎类型、当前引擎和识别来源。
* 保留最近识别结果历史。
* 支持的对话式图像描述引擎可以继续追问。
* 支持流式输出的引擎可以更快返回结果。
* 可按引擎单独启用或禁用。
* 使用 Windows DPAPI 保存凭据。

## 内置引擎

OCR 引擎：

* 百度 OCR
* Google Gemini OCR
* NVDACN 提供的 Vivo OCR
* PaddleOCR / PaddleOCR-VL
* 通过本地或托管 Ollama API 使用的 Ollama OCR

图像描述引擎：

* Google Gemini
* Google Gemma
* NVDACN 提供的 Vivo Image Describer
* NVDACN 提供的 VIVO BlueLLM Vision
* 通过本地或托管 Ollama API 使用的 Ollama Vision

部分引擎需要 API 密钥、服务地址或账号凭据。可在 `NVDA 菜单 -> 选项 -> Vis Aware 设置` 中配置。

## 基本使用

主要命令：

| 命令 | 默认手势 |
| --- | --- |
| 识别图像 | `NVDA+Alt+Space` |
| 对上一次图像描述追问 | `NVDA+Alt+Q` |
| 切换识别引擎类型 | `NVDA+Alt+1` |
| 在当前类型中切换引擎 | `NVDA+Alt+2` |
| 切换识别来源 | `NVDA+Alt+3` |

主识别命令会使用常规设置中的当前识别类型和来源。

* 按一次：用虚拟文档显示结果。
* 连按两次：用普通文本结果显示。

如果 OCR 结果包含坐标，可在虚拟文档中把光标移动到对应文本上，按 `Enter` 或 `Space` 尝试激活原屏幕位置。是否可用取决于引擎返回的坐标。

## 设置

打开 `NVDA 菜单 -> 选项 -> Vis Aware 设置`。

常规面板控制：

* 是否将识别结果复制到剪贴板
* 是否使用可浏览消息显示文本结果
* 是否自动朗读虚拟文档结果
* 识别来源顺序
* OCR 或图像描述模式
* 支持的 Vivo 引擎使用的 NVDACN 账号设置

每个引擎也有自己的设置面板。不需要的引擎可以禁用，之后日常使用和切换引擎时会跳过。

对于 Ollama 引擎，API 地址可以填写完整 URL，也可以只写主机和端口，比如 `localhost:11434`。默认 API 根地址是 `http://localhost:11434/api`。可在引擎设置里点击“获取模型”按钮加载本地模型名称，然后从模型列表中选择一个。若不手动选择模型，系统会使用 Ollama 返回的第一个可用模型。API 密钥为可选项，会作为 `Authorization: Bearer` 令牌发送，适用于托管或代理的 Ollama 部署。

## 说明

* Ollama 引擎需要已安装或托管的 Ollama 服务，以及支持视觉能力的模型。所选模型返回有效结构化 OCR 数据时，Ollama OCR 可在虚拟文档结果中提供可点击的屏幕坐标。流式输出和普通文本结果仍为纯文本。
* 追问和流式输出取决于具体引擎支持。

## 构建

在仓库根目录运行：

```powershell
uv run scons
```

更新翻译模板：

```powershell
uv run scons pot
```

## 许可

本项目使用 GNU General Public License version 2，详见 `COPYING.txt`。
