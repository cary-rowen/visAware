# Vis Aware

Vis Aware 是一款为 NVDA 提供 OCR、AI 图像描述、自动识别和 AI 辅助操作计算机的插件。

Vis Aware 需要 NVDA 2026.1 或更高版本。大多数托管引擎需要联网并提供相应服务的凭据；Ollama 用于使用本地自托管服务（例如 Google Gemma）。

## 快速上手

1. 打开 `NVDA 菜单 > 选项 > Vis Aware 设置...`。在**常规**面板中可登录 NVDA 中文站（www.nvdacn.com）账号，用于使用相关的免费社区服务。随后根据需要在相应的 **OCR**、**图像描述**或 **AI Agent** 面板中选择并配置其他引擎。
2. 按 `NVDA+alt+1` 选择模式（能力类型）：**OCR**、**图像描述**或 **AI Agent**。
3. 按 `NVDA+alt+2` 选择相应模式下支持的引擎，这里应当选择已经在步骤（1）中配置好的引擎。
4. 将导航对象移到要识别的内容上，或按 `NVDA+alt+3` 选择其他识别来源（例如：剪贴板中的图像或图像文件）。
5. 按一次 `NVDA+alt+space`，按照上述选择的能力类型执行识别或启动/终止 Agent。对于普通识别：
   - 按一次用 NVDA 的识别结果文档呈现：对于 OCR 结果，支持用 `Enter` 或 `Space` 点击文字对应的坐标位置，适用于窗口识别。
   - 连按两次的行为取决于“常规设置”中的“在浏览模式下显示文本结果”是否选中：若选中该选项，则在 NVDA 的浏览模式对话框中呈现，适用于数学公式、表格或需要 Markdown 渲染的结构化内容；若不选中，NVDA 则只会朗读识别结果，并不显示任何对话框。

## 模式和识别来源

Vis Aware 提供三种模式：

* **OCR**：从图像中提取文字。
* **图像描述**：描述图像内容。支持追问的引擎可以回答与上一次描述有关的问题。
* **AI Agent**：分析屏幕并根据任务执行计算机操作。

OCR 和图像描述可使用以下识别来源：

* 导航对象
* 整个屏幕
* 前台窗口
* 剪贴板中的图像或图像文件

AI Agent 模式直接截取全屏幕，不使用已选择的识别来源。

## 命令

以下命令具有默认手势：

| 命令 | 默认手势 |
| --- | --- |
| 按当前设置执行识别 | `NVDA+alt+space` |
| 对上一次图像描述进行追问 | `NVDA+alt+q` |
| 循环切换识别模式 | `NVDA+alt+1` |
| 循环切换当前模式的引擎 | `NVDA+alt+2` |
| 循环切换识别来源 | `NVDA+alt+3` |

以下命令没有默认手势：

* 描述当前导航对象的内容
* 描述剪贴板中的图像
* 使用 OCR 识别当前导航对象的内容
* 使用 OCR 识别剪贴板图像中的文字
* 显示上一次识别结果
* 取消当前识别

为**描述剪贴板中的图像**分配手势后，按一次用 NVDA 的识别结果文档呈现；快速连按两次时，根据“在浏览模式下显示文本结果”设置，在浏览模式下显示或仅朗读识别结果。

## 识别结果

如果 OCR 结果包含坐标，在识别结果文档中的文字上按 `enter` 或 `space`，会单击识别区域在屏幕上的对应位置。

识别结果可以用 NVDA 的虚拟文档显示，也可以按设置在浏览模式对话框中显示。浏览模式会呈现所支持的 Markdown 和数学公式。还可以将结果复制到剪贴板。

当前 NVDA 会话仅保留上一次识别结果。对于支持追问的图像描述引擎，追问命令会打开多轮对话窗口，并在引擎支持时流式朗读回答。在该窗口中，按 `control+enter` 发送问题，按 `escape` 取消请求或关闭窗口。使用**查看格式化内容**可在浏览模式下查看初始图像描述或最新回答。

Google Gemini、Google Gemma、VIVO BlueLLM Vision 和 Ollama Vision 支持追问。对于非对话窗口，流式输出仅在未使用浏览模式的情况下生效。

## 自动识别

自动识别默认关闭。在**自动识别**面板中，可以选择 OCR 或图像描述，再选择当前引擎或一个已启用的特定引擎。提示词和模型仅覆盖该引擎用于自动识别时的设置。提示词留空会使用引擎的常规提示词；选择**使用引擎设置中选择的模型**会沿用其常规模型。引擎支持时，可以使用**获取模型**加载可用模型名称。

当系统焦点、浏览模式光标或导航对象移到受支持的图形类控件时，自动识别会在后台运行。结果会自动朗读并保存为上一次识别结果；所用引擎支持时可以继续追问。

对于网页图形，Vis Aware 通常使用图片的 URL；没有可用 URL 时改用对象截图。启用**优先截取网页图像**后，会先识别屏幕上显示的内容，无法截图时再使用 URL。

## AI Agent 模式

AI Agent 会先询问要完成的任务，从前台窗口开始，并可跨窗口操作。AI Agent 可以单击、输入文字、按键、滚动、拖放、导航和等待，并在需要信息时询问用户。每项操作执行前不会逐一确认，请留意其操作，并在需要时将其停止。

AI Agent 每一步都会向所选服务发送全屏截图，其中可能包含其他窗口的内容。请避免在屏幕上显示敏感信息时运行 AI Agent。

启用黑屏时无法启动 AI Agent。

## 设置

打开 `NVDA 菜单 > 选项 > Vis Aware 设置...`。

设置对话框包含以下面板：

* **常规**：结果显示、复制到剪贴板、自动朗读、调试日志、循环切换时包含的识别来源、Vis Aware 模式和 NVDACN 账户。
* **自动识别**：自动识别类型、引擎、提示词、模型、获取模型和网页图像截图首选项。
* **OCR**、**图像描述**和 **AI Agent**：当前引擎、引擎启用状态和各引擎提供的设置。

日常使用和切换引擎时会跳过已禁用的引擎。每种模式至少需要保留一个已启用的引擎。

PaddleOCR / PaddleOCR-VL 支持 AI Studio 托管任务 API、AI Studio 部署服务和自托管 PaddleOCR 服务。

对于 Ollama 引擎，可以填写完整 API URL，也可以填写主机和端口，例如 `localhost:11434`。默认 API 根地址为 `http://localhost:11434/api`。使用**获取模型**加载模型名称，然后选择模型。如果没有选择模型，将使用 Ollama 返回的第一个模型。可选的 API 密钥会作为 `Authorization: Bearer` 令牌发送。Ollama 引擎需要支持视觉能力的模型，例如 Gemma 4；仅当模型返回有效的结构化 OCR 数据时，Ollama OCR 才会提供屏幕坐标。

## 内置引擎

OCR 引擎：

* Apple Vision (OCR Server)
* 百度 OCR
* Google Gemini OCR
* Vivo OCR（NVDACN）
* PaddleOCR / PaddleOCR-VL
* Ollama OCR

图像描述引擎：

* Google Gemini
* Google Gemma
* Vivo Image Describer（NVDACN）
* VIVO BlueLLM Vision（NVDACN）
* Ollama Vision

AI Agent 引擎：

* Google Gemini
* OpenAI
* Vivo BlueLM Vision（NVDACN）

引擎是否可用取决于相应服务和配置。

## Apple Vision (OCR Server)

Apple Vision (OCR Server) 是一个局域网 OCR 引擎。Vis Aware 会将所选图像发送到开源 iOS 应用 [OCR Server](https://github.com/riddleling/iOS-OCR-Server)，由该应用使用 Apple Vision 识别文字并返回文字坐标。该引擎不需要云端 OCR 账号，但电脑和 iPhone 必须能够通过同一局域网相互访问。

当前版本的应用要求 iOS 18.4 或更高版本，支持的最早机型为 iPhone XS、iPhone XS Max、iPhone XR 和第二代 iPhone SE。

配置步骤：

1. 在 iPhone 上从 [App Store](https://apps.apple.com/us/app/ocr-server/id6749533041) 安装 OCR Server。
2. 将 iPhone 和电脑连接到同一局域网，然后打开 OCR Server。服务器会自动启动并显示连接地址。
3. 打开 `NVDA 菜单 > 选项 > Vis Aware 设置... > OCR`，启用并选择 **Apple Vision (OCR Server)**，然后填写应用显示的地址，例如 `192.168.1.10:8000`。

与 OCR Server 的连接使用未经身份验证且未加密的 HTTP。请仅在可信局域网中使用此引擎；在公共或不可信网络上，上传的图像和返回的 OCR 结果可能被截获或篡改。

使用该引擎时，请保持 OCR Server 在前台运行并让 iPhone 屏幕常亮。如需长时间连续运行，可按照原项目说明开启 iOS 引导式访问。应用使用方法和服务器 API 详见原项目说明。

## 推荐的离线方案：通过 Ollama 使用 Gemma 4

如需离线、自托管方案，推荐在本机 Ollama 中运行具备视觉能力的 Gemma 4 模型。模型下载完成且 API URL 指向本地服务后，Vis Aware 会将图像发送到该本地服务。如果使用他人托管的远程 Ollama 地址，数据会发送到该远程服务。


## 数据和凭据

识别会将所选图像以及适用时的提示词发送给所选引擎配置的服务。AI Agent 每一步都会向所选服务发送全屏截图，其中可能包含其他窗口的内容。请查阅相应服务的数据政策，并避免发送敏感内容。

除剪贴板来源外，手动识别要求关闭黑屏。NVDACN 密码使用 Windows DPAPI 保护；其他保存的 API 密钥会以明文形式存储在 NVDA 配置中，在创建或分享 NVDA 便携版之前请务必留意数据安全。

## 许可

本项目使用 GNU General Public License version 2，详见 `COPYING.txt`。
