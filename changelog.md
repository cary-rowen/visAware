### 0.9.5

* Added Baimiao OCR and formula recognition, including shared account login, protected sessions, clickable text coordinates, and accessible MathML formula output.
* Added screen capture through NVDA's Windows Graphics Capture path, allowing screen recognition and AI Agent screenshots while Screen Curtain or the built-in Magnifier is active on supported systems.
* Improved Baimiao account settings and documentation.
* Improved automatic recognition settings so the selected engine is preserved correctly.

### 0.9.0

* Added DeepSeek image description and AI Agent engines, with configurable API endpoints and vision models.
* Added a command to cycle automatic recognition between off, image description, and OCR; it has no default gesture.
* Improved automatic recognition reliability by serializing background work, keeping only the latest pending task, propagating cancellation through network and OCR requests, and using shorter request timeouts.
* Improved browse-mode automatic recognition for web graphics by recognizing current and lazy-loaded image URLs and falling back to screenshots when URLs are unavailable.
* Improved automatic recognition result handling to ignore results after the active recognition target changes.

### 0.8.1

* Added Kimi image description, follow-up questions, OCR with structured coordinates, and AI Agent support through the OpenAI-compatible Kimi API.
* Improved image-description prompts across supported engines with localized defaults.
* Improved mathematical image descriptions by converting visible formulas to LaTeX and returning formula-only images as LaTeX formulas.
* Improved automatic image recognition with concise 20 to 30 word descriptions without Markdown.
* Improved browsable image-description output by using standard Markdown tables for tabular content and avoiding code fences.
* Removed model and model-provider names from the follow-up dialog.
* Added Copy and Close buttons to browsable recognition and follow-up result dialogs.

### 0.7.0

* Added the Apple Vision (OCR Server) engine for local-network OCR through the open-source OCR Server iOS app.

### 0.6.5

* Improved settings and follow-up dialog layouts, including high-DPI scaling.
* Improved editing of long custom and automatic-recognition prompts.
* Improved follow-up questions so initial descriptions and latest answers can be viewed as formatted content.

### 0.6.4

* Improved formula rendering in HTML and PaddleOCR results.
* Fixed AI Agent text input and automatic recognition on English interfaces.
* Improved English and Simplified Chinese UI text and documentation.

### 0.6.2

* Updated the available Gemini models, with Gemini 3.6 Flash as the new default and Gemini 3.5 Flash-Lite as a low-cost option.

### 0.6.1

* Removed the separate AI Agent start/stop command from Input Gestures; use the main Vis Aware command in Agent mode.
* Updated the English and Simplified Chinese documentation for commands, automatic recognition, settings, data handling, and the recommended Gemma 4 setup through Ollama.

### 0.6.0

* Added PaddleOCR / PaddleOCR-VL and Google Gemma engines.
* Added Markdown rendering for supported recognition and image description results.
* Added follow-up question support for conversational image description engines.
* Added per-engine enable / disable controls so unused engines can be hidden from normal use and engine cycling.
* Improved engine settings handling and result presentation reliability.
