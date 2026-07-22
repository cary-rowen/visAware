# Vis Aware

Vis Aware 是一款为 NVDA 提供 OCR、图像描述、自动识别和 AI 辅助计算机操作的插件。

Vis Aware 需要 NVDA 2026.1 或更高版本。大多数托管引擎需要联网并提供相应服务的凭据；Ollama 可使用本地或托管服务。

## 模式和识别来源

Vis Aware 提供三种模式：

* **OCR 引擎**：从图像中提取文字。
* **图像描述引擎**：描述图像内容。支持追问的引擎可以回答与上一次描述有关的问题。
* **AI Agent**：分析屏幕并根据任务执行计算机操作。

OCR 和图像描述可以使用以下识别来源：

* 导航对象
* 整个屏幕
* 当前窗口
* 剪贴板中的图像数据或图像文件

AI Agent 模式直接使用屏幕，不使用已选择的识别来源。

## 内置引擎

OCR 引擎：

* 百度 OCR
* Google Gemini OCR
* Vivo OCR (NVDACN)
* PaddleOCR / PaddleOCR-VL
* Ollama OCR

图像描述引擎：

* Google Gemini
* Google Gemma
* Vivo Image Describer (NVDACN)
* VIVO BlueLLM Vision (NVDACN)
* Ollama Vision

AI Agent 引擎：

* Google Gemini
* OpenAI
* Vivo BlueLM Vision (NVDACN)

引擎是否可用取决于相应服务和配置。

## 推荐的离线方案：通过 Ollama 使用 Gemma 4

如需离线、自托管方案，推荐在本机 Ollama 中运行具备视觉能力的 Gemma 4 模型。模型下载完成且 API URL 指向本地服务后，Vis Aware 会将图像发送到该本地服务。如果使用他人托管的远程 Ollama 地址，数据会发送到该远程服务。

使用 **Ollama Vision** 可以进行图像描述、OCR、图表或数学公式识别。识别结果对话框可以将其呈现为完全可供屏幕阅读器导航的数学公式/表格。

Google Gemma 引擎当前提供 **Gemma 4 26B A4B IT**（推荐，较快）和 **Gemma 4 31B IT**（质量更高，较慢）。Ollama 中的模型名称由已安装的模型决定，请使用**获取模型**进行选择。

## 命令

以下命令具有默认手势：

| 命令 | 默认手势 |
| --- | --- |
| 按当前设置运行 Vis Aware | `NVDA+alt+space` |
| 对上一次图像描述追问 | `NVDA+alt+q` |
| 切换 Vis Aware 模式 | `NVDA+alt+1` |
| 切换当前模式中的引擎 | `NVDA+alt+2` |
| 切换识别来源 | `NVDA+alt+3` |

以下命令没有默认手势：

* 描述当前导航对象的内容。
* 描述剪贴板中的图像。
* 使用 OCR 识别当前导航对象的内容。
* 使用 OCR 识别剪贴板图像中的文字。
* 显示上一次识别结果。
* 取消当前识别。

为**描述剪贴板中的图像**分配手势后，按一次打开识别结果文档，快速连按两次返回纯文本。

可以在 `NVDA 菜单 > 选项 > 按键与手势...` 对话框的 `Vis Aware` 类别中分配或更改手势。

在 OCR 和图像描述模式下，主命令使用已选择的引擎和来源。按一次打开 NVDA 识别结果文档，快速连按两次返回纯文本。在 AI Agent 模式下，该命令打开任务输入对话框；AI Agent 运行时再次执行该命令会将其停止。

切换识别来源命令在 AI Agent 模式下不可用。该命令按上面列出的固定顺序循环，只包含常规设置面板中勾选的来源。

## 识别结果

如果 OCR 结果包含坐标，可以在识别结果文档中按 `enter` 或 `space`，激活（通常为单击）光标处的文字。

纯文本结果默认直接朗读，也可以按设置用可浏览消息显示。可浏览消息会呈现所支持的 Markdown 和数学公式。还可以将结果复制到剪贴板，并自动朗读识别结果文档。

当前 NVDA 会话仅保留上一次识别结果。对于支持追问的图像描述引擎，追问命令会打开多轮对话窗口，并在引擎支持时流式朗读回答。在该窗口中，按 `control+enter` 发送问题，按 `escape` 取消请求或关闭窗口。使用**打开渲染回答**可在已格式化的可浏览消息中查看回答。

Google Gemini、Google Gemma、VIVO BlueLLM Vision 和 Ollama Vision 支持追问。对于主识别结果，启用流式输出且未使用可浏览消息时，Google Gemini 图像描述、VIVO BlueLLM Vision、Ollama Vision 和 Ollama OCR 支持逐段朗读结果。

## 自动识别

自动识别默认关闭。在自动识别面板中，可以选择 OCR 或图像描述，并选择当前引擎或一个已启用的指定引擎。引擎支持时，还可以单独设置提示词和模型，并获取可用模型名称。

当系统焦点、浏览模式光标或导航对象移到受支持的图像、图形或图像式控件时，自动识别会在后台运行；结果自动朗读并保存为上一次结果，所用引擎支持时可继续追问。

## AI Agent 模式

AI Agent 会先询问要完成的任务，从当前前台状态开始，并可跨窗口操作。AI Agent 可以单击、输入文字、按键、滚动、拖放、导航和等待，并在需要信息时询问用户。每项操作执行前不会逐一确认，请留意其操作，并在需要时将其停止。

启用黑屏时无法启动 AI Agent。

## 设置

打开 `NVDA 菜单 > 选项 > 打开 Vis Aware 设置`。

设置对话框包含以下面板：

* **常规设置**：结果显示、复制到剪贴板、自动朗读、调试日志、参与来源循环的识别来源、Vis Aware 模式和 NVDACN 账号。
* **自动识别**：自动识别类型、引擎、提示词、模型、获取模型和网页图像截图首选项。
* **OCR**、**图像描述**和**Agent**：当前引擎、引擎启用状态和各引擎提供的设置。

日常使用和切换引擎时会跳过已禁用的引擎。每种模式至少需要保留一个已启用的引擎。

PaddleOCR / PaddleOCR-VL 支持 AI Studio 托管任务 API、AI Studio 部署服务和自托管 PaddleOCR 服务。

对于 Ollama 引擎，可以填写完整 API URL，也可以填写主机和端口，例如 `localhost:11434`。默认 API 根地址为 `http://localhost:11434/api`。使用**获取模型**加载模型名称，然后选择模型。如果没有选择模型，将使用 Ollama 返回的第一个模型。可选的 API 密钥会作为 `Authorization: Bearer` 令牌发送。Ollama 引擎需要支持视觉能力的模型，例如 Gemma 4；仅当模型返回有效的结构化 OCR 数据时，Ollama OCR 才会提供屏幕坐标。

## 数据和凭据

识别会将所选图像以及适用时的提示词发送给所选引擎配置的服务。AI Agent 每一步都会向所选服务发送所有显示器组成的整个屏幕区域截图，其中可能包含其他窗口的内容。请查阅相应服务的数据政策，并避免发送敏感内容。

除剪贴板来源外，手动识别要求关闭黑屏。NVDACN 密码使用 Windows DPAPI 保护；其他已保存的 API 密钥和令牌存储在 NVDA 配置中。

## 许可

本项目使用 GNU General Public License version 2，详见 `COPYING.txt`。
