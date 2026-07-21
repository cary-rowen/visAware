# Vis Aware

Vis Aware is an NVDA add-on for OCR and image description. It can recognize
content from the navigator object, the clipboard, the foreground window, or
the full screen, and then present the result through NVDA.

The add-on is still under active development. Interfaces, settings, and engine
support may change between releases.

## Main Features

* OCR and image description engines in one add-on.
* Recognition sources:
  * navigator object
  * clipboard image or image file
  * foreground window
  * whole screen
* Result output options:
  * NVDA virtual document
  * browseable message
  * clipboard copy
  * automatic reading of virtual document results
* Engine switching and source switching from input gestures.
* Recent result history.
* Follow-up questions for supported conversational image description engines.
* Streaming output for engines that support it.
* Per-engine enable / disable controls.
* Credential storage using Windows DPAPI.

## Included Engines

OCR engines:

* Baidu OCR
* Google Gemini OCR
* Vivo OCR through NVDACN
* PaddleOCR / PaddleOCR-VL
* Ollama OCR through a local or hosted Ollama API

Image description engines:

* Google Gemini
* Google Gemma
* Vivo Image Describer through NVDACN
* VIVO BlueLLM Vision through NVDACN
* Ollama Vision through a local or hosted Ollama API

Some engines require API keys, service URLs, or account credentials. Configure
them from `NVDA menu -> Preferences -> Vis Aware settings`.

## Basic Use

Main commands:

| Command | Default Gesture |
| --- | --- |
| Recognize image | `NVDA+Alt+Space` |
| Ask a follow-up question | `NVDA+Alt+Q` |
| Cycle recognition engine type | `NVDA+Alt+1` |
| Cycle engine within the current type | `NVDA+Alt+2` |
| Cycle recognition source | `NVDA+Alt+3` |

The main recognition command uses the current engine type and source from the
general settings.

* Press once for a virtual document result.
* Press twice for a simple text result.

For OCR results with coordinate data, pressing `Enter` or `Space` on recognized
text in the virtual document can activate the corresponding screen position.
This depends on the engine returning usable coordinates.

## Settings

Open `NVDA menu -> Preferences -> Vis Aware settings`.

The general panel controls:

* whether recognition results are copied to the clipboard
* whether text results are shown in a browseable message
* whether virtual document results are automatically read
* recognition source order
* OCR or image description mode
* NVDACN account settings for supported Vivo engines

Each engine also has its own settings panel. Unused engines can be disabled so
they are skipped during normal use and cycling.

For Ollama engines, the API URL can be a full URL or a host and port such as
`localhost:11434`. The default API root is `http://localhost:11434/api`. Use
the Fetch models button in the engine settings to load local model names, then
choose one from the model list. If no model is selected, the first available
model returned by Ollama is used. The API key field is optional and is sent as
an `Authorization: Bearer` token for hosted or proxied Ollama deployments.

## Notes

* Ollama engines require an installed or hosted Ollama service and a vision
  capable model. The OCR engine can provide clickable screen coordinates in
  virtual document results when the selected model returns valid structured
  OCR data. Streaming and simple text results are text-only.
* Streaming output and follow-up questions depend on engine support.

## Build

From the repository root:

```powershell
uv run scons
```

To update translation templates:

```powershell
uv run scons pot
```

## License

This project is licensed under the GNU General Public License version 2. See
`COPYING.txt` for details.
