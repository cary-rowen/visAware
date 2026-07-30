# Vis Aware

Vis Aware is an NVDA add-on for OCR, AI-powered image description, automatic
recognition, and AI-assisted computer control.

Vis Aware requires NVDA 2026.1 or later. Most hosted engines require an
Internet connection and service credentials. Ollama is used for local
self-hosting (for example, Google Gemma).

## Quick start

1. Open `NVDA menu > Preferences > Vis Aware settings...`. In **General**,
   you can sign in to your NVDA Chinese Community (www.nvdacn.com) account to
   use the related free community services. Then select and configure other
   engines as needed in the corresponding **OCR**, **Image description**, or
   **AI Agent** panel.
2. Press `NVDA+alt+1` to select a mode (capability type): **OCR**, **Image
   description**, or **AI Agent**.
3. Press `NVDA+alt+2` to select an engine supported by that mode. Choose an
   engine that you configured in step 1.
4. Move the navigator object to the content you want to recognize, or press
   `NVDA+alt+3` to select another recognition source (for example, an image or
   image file on the clipboard).
5. Press `NVDA+alt+space` once to perform recognition using the selected
   capability or start or stop the Agent. For regular recognition:

   - Press once to present the result in an NVDA recognition result document.
     For OCR results, you can use `Enter` or `Space` to click the coordinates
     corresponding to the text; this is useful when recognizing a window.
   - The behavior of pressing twice depends on whether **Show text results in a
     browsable message** is selected in **General** settings. If selected, the
     result is presented in an NVDA browse mode dialog, which is suitable for
     mathematical formulas, tables, or structured content that requires
     Markdown rendering. If not selected, NVDA only announces the recognition
     result and does not display a dialog.

## Modes and recognition sources

Vis Aware provides three modes:

* **OCR** extracts text from an image.
* **Image description** describes visual content. Supported engines can answer
  follow-up questions about the previous description.
* **AI Agent** analyzes the screen and performs computer actions for a task.

OCR and image description can use these recognition sources:

* Navigator object
* Whole screen
* Foreground window
* Image or image file on the clipboard

AI Agent mode captures the full screen directly and does not use the selected
recognition source.

## Commands

The following commands have default gestures:

| Command | Default gesture |
| --- | --- |
| Perform recognition using the current settings | `NVDA+alt+space` |
| Ask a follow-up question about the previous image description | `NVDA+alt+q` |
| Cycle through recognition modes | `NVDA+alt+1` |
| Cycle through engines for the current mode | `NVDA+alt+2` |
| Cycle through recognition sources | `NVDA+alt+3` |

The following commands have no default gesture:

* Describes the content of the current navigator object
* Describes images on the clipboard
* Recognizes the content of the current navigator object using OCR
* Recognizes text in images on the clipboard using OCR
* Shows the previous recognition result
* Cancels the current recognition

After assigning a gesture to **Describes images on the clipboard**, press it
once to present the result in an NVDA recognition result document. Press it
twice in quick succession to show the result in a browsable message or have
NVDA announce it, depending on the **Show text results in a browsable message**
setting.

## Recognition results

When OCR results include coordinates, pressing `enter` or `space` on text in
the recognition result document clicks the corresponding location in the
recognized screen area.

Recognition results can be displayed in an NVDA virtual document or, depending
on the setting, in a browse mode dialog. Browse mode renders supported Markdown
and mathematical formulas. Results can also be copied to the clipboard.

Only the previous recognition result from the current NVDA session is retained.
For image description engines that support follow-up questions, the follow-up
question command opens a multi-turn dialog and streams spoken answers when
supported. In that dialog, `control+enter` sends a question and `escape`
cancels the request or closes the dialog. Use **View formatted content** to view
the initial description or latest answer in browse mode.

Follow-up questions are supported by Google Gemini, Google Gemma, VIVO BlueLLM
Vision, and Ollama Vision. Outside the conversation dialog, streaming output is
available only when browse mode is not in use.

## Automatic recognition

Automatic recognition is off by default. In the **Automatic recognition**
panel, choose OCR or image description, then choose the current engine or a
specific enabled engine. The prompt and model fields override that engine only
for automatic recognition. Leave the prompt blank to use the engine's regular
prompt, and choose **Use the model selected in engine settings** to follow its
regular model. When supported, use **Fetch models** to load available model
names.

Automatic recognition runs in the background when the system focus, browse
mode cursor, or navigator object moves to a supported graphic control. The
result is announced automatically and saved as the previous result; follow-up
questions are available when supported by the engine.

For web graphics, Vis Aware normally uses the image's URL and uses an object
screenshot instead when no URL is available. **Prefer screenshots for web
images** recognizes the content shown on the screen first and falls back to the
URL if a screenshot cannot be captured.

## AI Agent mode

The AI Agent asks for a task, starts from the foreground window, and can
operate across windows. It can click, type, press keys, scroll, drag and drop,
navigate, wait, and ask you for information when needed. Actions are not
confirmed one by one, so monitor the session and stop it when necessary.

Each AI Agent step sends the selected service a full-screen screenshot, which
can include content from other windows. Avoid running the AI Agent while
sensitive information is visible on the screen.

The AI Agent cannot start while Screen Curtain is enabled.

## Settings

Open `NVDA menu > Preferences > Vis Aware settings...`.

The settings dialog contains these panels:

* **General**: result output, clipboard copying, automatic reading, debug
  logging, the sources included when cycling, the Vis Aware mode, and NVDACN
  account details.
* **Automatic recognition**: automatic recognition type, engine, prompt,
  model, fetching model names, and web-image screenshot preference.
* **OCR**, **Image description**, and **AI Agent**: the current engine, engine
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

## Included engines

OCR engines:

* Apple Vision (OCR Server)
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

## Apple Vision (OCR Server)

Apple Vision (OCR Server) is a local-network OCR engine. Vis Aware sends the
selected image to [OCR Server](https://github.com/riddleling/iOS-OCR-Server),
an open-source iOS app that recognizes text with Apple Vision and returns text
coordinates. No cloud OCR account is required, but the computer and iPhone must
be able to reach each other on the same local network.

The current app requires iOS 18.4 or later. The oldest supported iPhones are
iPhone XS, iPhone XS Max, iPhone XR, and iPhone SE (2nd generation).

To configure the engine:

1. Install [OCR Server from the App Store](https://apps.apple.com/us/app/ocr-server/id6749533041)
   on the iPhone.
2. Connect the iPhone and computer to the same local network, then open OCR
   Server. Its server starts automatically and displays the address to use.
3. Open `NVDA menu > Preferences > Vis Aware settings... > OCR`, enable and
   select **Apple Vision (OCR Server)**, then enter the displayed address, such
   as `192.168.1.10:8000`.

OCR Server connections use unauthenticated, unencrypted HTTP. Use this engine
only on a trusted local network; on public or untrusted networks, transmitted
images and returned OCR results can be intercepted or modified.

Keep OCR Server open and the iPhone screen on while using the engine. For
continuous operation, follow the upstream project's instructions for iOS
Guided Access. The upstream project also documents app usage and the server API.

## Recommended offline setup: Gemma 4 through Ollama

For an offline, self-hosted setup, we recommend running a vision-capable Gemma
4 model locally with Ollama. After the model is downloaded and the API URL
points to the local service, Vis Aware sends images to that local service. If
you use a remote Ollama address hosted by someone else, data is sent to that
remote service.

## Data and credentials

Recognition sends the selected image and, when applicable, a prompt to the
service configured for the selected engine. Each AI Agent step sends a
full-screen screenshot to the selected service; the screenshot can include
other windows. Review the service's data policy and avoid sending sensitive
content.

Manual recognition from a source other than the clipboard requires Screen
Curtain to be disabled. The NVDACN password is protected with Windows DPAPI.
Other saved API keys are stored unencrypted in the NVDA configuration. Be
mindful of data security before creating or sharing a portable copy of NVDA.

## License

This project is licensed under the GNU General Public License version 2. See
`COPYING.txt` for details.
