# Vis Aware

Vis Aware is an NVDA add-on for OCR, image description, automatic recognition,
and AI-assisted computer operation.

Vis Aware requires NVDA 2026.1 or later. Most hosted engines require an
Internet connection and service credentials. Ollama can use a local or hosted
service.

## Modes and recognition sources

Vis Aware provides three modes:

* **OCR Engines** extract text from an image.
* **Image Describer Engines** describe visual content. Supported engines can
  answer follow-up questions about the previous description.
* **AI Agent** analyzes the screen and performs computer actions for a task.

OCR and image description can use these recognition sources:

* Navigator object
* The whole screen
* Current foreground window
* Image data or image file in clipboard

The AI Agent mode uses the screen directly and does not use the selected
recognition source.

## Included engines

OCR engines:

* Baidu OCR
* Google Gemini OCR
* Vivo OCR (NVDACN)
* PaddleOCR / PaddleOCR-VL
* Ollama OCR

Image description engines:

* Google Gemini
* Google Gemma
* Vivo Image Describer (NVDACN)
* VIVO BlueLLM Vision (NVDACN)
* Ollama Vision

AI Agent engines:

* Google Gemini
* OpenAI
* Vivo BlueLM Vision (NVDACN)

Engine availability depends on its service and configuration.

## Recommended offline setup: Gemma 4 through Ollama

For an offline, self-hosted setup, we recommend running a vision-capable Gemma
4 model locally with Ollama. After the model is downloaded and the API URL
points to the local service, Vis Aware sends images to that local service. If
you use a remote Ollama address hosted by someone else, data is sent to that
remote service.

Use **Ollama Vision** for image description, OCR, and recognition of charts or
mathematical formulas. When text results are shown in a browsable message,
supported formulas and tables are available for navigation with a screen reader.

The Google Gemma engine currently provides **Gemma 4 26B A4B IT** (recommended,
faster) and **Gemma 4 31B IT** (higher quality, slower). Ollama model names
depend on the models installed in Ollama; use **Fetch models** to choose one.

## Commands

The following commands have default gestures:

| Command | Default gesture |
| --- | --- |
| Run Vis Aware using the current settings | `NVDA+alt+space` |
| Ask a follow-up question about the previous image description | `NVDA+alt+q` |
| Cycle Vis Aware mode | `NVDA+alt+1` |
| Cycle the engine for the current mode | `NVDA+alt+2` |
| Cycle the recognition source | `NVDA+alt+3` |

The following commands have no default gesture:

* Describe the content of the current navigator object.
* Describe clipboard images.
* Recognize the content of the current navigator object with OCR.
* Recognize text in a clipboard image with OCR.
* Show the previous recognition result.
* Cancel the current recognition.

After assigning a gesture to **Describe clipboard images**, press it
once to open a recognition result document or twice in quick succession for a
plain-text result.

Assign or change gestures in the `NVDA menu > Preferences > Input gestures...`
dialog, under the `Vis Aware` category.

In OCR and image description modes, the main command uses the selected engine
and source. Press it once to open an NVDA recognition result document, or twice
in quick succession for a plain-text result. In AI Agent mode, it opens a task
prompt; pressing the command again while the agent is running stops it.

The source cycling command is unavailable in AI Agent mode. It follows the
fixed order listed above and includes only the sources checked in the General
settings panel.

## Recognition results

OCR results with coordinates can be activated from the recognition result
document: when coordinates are available, press `enter` or `space` to activate
(normally click) the text at the cursor.

Plain-text results are announced directly by default, or shown in a browsable
message when that option is enabled. Browsable messages render supported
Markdown and mathematical formulas. Results can also be copied to the
clipboard, and recognition result documents can be read automatically.

Only the previous recognition result from the current NVDA session is retained.
The follow-up question command opens a multi-turn dialog for supported image
description engines and streams spoken answers when supported. In that dialog,
`control+enter` sends a question and `escape` cancels the request or closes the
dialog. Use **Open rendered answer** to view the answer as a formatted
browsable message.

Follow-up questions are supported by Google Gemini, Google Gemma, VIVO BlueLLM
Vision, and Ollama Vision. For main recognition results, streaming speech is
supported by Google Gemini image description, VIVO BlueLLM Vision, Ollama
Vision, and Ollama OCR when streaming is enabled and browsable messages are
not in use.

## Automatic recognition

Automatic recognition is off by default. In the Automatic Recognition panel,
choose OCR or image description, then choose the current engine or a specific
enabled engine. When supported, you can set a separate prompt and model and
fetch the available model names.

Automatic recognition runs in the background when the system focus, browse
mode cursor, or navigator object moves to a supported image, graphic, or
image-like control. The result is announced automatically and saved as the
previous result; follow-up questions are available when supported by the
engine.

For web graphics, Vis Aware normally uses the image URL exposed by the object
and falls back to an object screenshot when no usable URL is available.
**Prefer screenshots for web image objects** uses the visible rendering first
and falls back to the URL if the screenshot cannot be captured.

## AI Agent mode

The AI Agent asks for a task, starts from the current foreground state, and can
operate across windows. It can click, type, press keys, scroll, drag and drop,
navigate, wait, and ask you for information when needed. Actions are not
confirmed one by one, so monitor the session and stop it when necessary.

The AI Agent cannot start while Screen Curtain is enabled.

## Settings

Open `NVDA menu > Preferences > Open Vis Aware settings`.

The settings dialog contains these panels:

* **General**: result output, clipboard copying, automatic reading, debug
  logging, the sources included in source cycling, the Vis Aware mode, and
  NVDACN account details.
* **Automatic Recognition**: automatic recognition type, engine, prompt,
  model, fetching model names, and web-image screenshot preference.
* **OCR**, **Image Describer**, and **Agent**: the current engine, engine
  enablement, and settings provided by each engine.

Disabled engines are skipped during normal use and engine cycling. At least one
engine must remain enabled in each mode.

For PaddleOCR / PaddleOCR-VL, the OCR settings support an AI Studio hosted task
API, an AI Studio deployed service, or a self-hosted PaddleOCR service.

For Ollama engines, enter a full API URL or a host and port such as
`localhost:11434`. The default API root is `http://localhost:11434/api`. Use
**Fetch models** to load model names and then choose a model. If no model is
selected, the first model returned by Ollama is used. The optional API key is
sent as an `Authorization: Bearer` token. Ollama engines require a
vision-capable model, such as Gemma 4; Ollama OCR provides screen coordinates
only when the model returns valid structured OCR data.

## Data and credentials

Recognition sends the selected image and, when applicable, a prompt to the
service configured for the selected engine. Each AI Agent step sends a
screenshot of the complete screen area across all displays to the selected
service; the screenshot can include other windows. Review the service's data
policy and avoid sending sensitive content.

Manual recognition from a source other than the clipboard requires Screen
Curtain to be disabled. The NVDACN password is protected with Windows DPAPI.
Other saved API keys and tokens are stored in the NVDA configuration.

## License

This project is licensed under the GNU General Public License version 2. See
`COPYING.txt` for details.
