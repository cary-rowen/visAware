# Vis Aware

Vis Aware is an NVDA add-on for OCR, AI-powered image description, automatic
recognition, and AI-assisted computer control.

Most engines require an Internet connection and service credentials. Vis Aware
also includes RapidOCR for fully offline use. You can use Ollama to self-host
local models such as Google's open-source Gemma models.

## Quick start

1. Open `NVDA menu > Preferences > Vis Aware settings...`. In the **General**
   panel, enter your [NVDACN account](https://nvdacn.com/admin/login.php) to
   use the related free community services, which currently include Vivo image
   description, OCR, and general-purpose language model services.
    - If you have API access from another model provider, such as Kimi,
      DeepSeek, or Gemini, configure it in the corresponding **OCR**, **Image
      description**, or **AI Agent** panel.
    - Click **OK** to save the settings.
2. Press `NVDA+alt+1` to select a mode: **OCR**, **Image description**, or
   **AI Agent**.
3. Press `NVDA+alt+2` to select an engine for the current mode. Choose an
   engine configured in step 1. If you signed in to NVDACN, you can choose a
   Vivo engine.
4. Press `NVDA+alt+3` to select a recognition source: **Navigator object** or
   **Image or image file on the clipboard**.
5. Press `NVDA+alt+space` to start recognition using the selected mode:
    - A single press presents the result in an NVDA recognition result
      document. For OCR results, press `Enter` or `Space` on text to click its
      corresponding location. This is useful for custom-drawn windows that are
      not accessible to screen readers.
    - The behavior of pressing twice depends on whether **Show text results in
      a browsable message** is selected in **General**. When selected, the
      result is shown in an NVDA browse mode dialog, which is useful for
      mathematical formulas, tables, and other content that requires Markdown
      rendering. Otherwise, NVDA announces the result without opening a dialog.
    - In **AI Agent** mode, enter a task for the AI to perform. This is useful
      for custom-drawn windows that are not accessible to screen readers and
      for complex, multi-step tasks.
6. While a task is running, press `NVDA+alt+space` at any time to stop it or
   start a new recognition task.

## Modes and recognition sources

Vis Aware provides three modes:

* **OCR** extracts text from an image.
* **Image description** describes visual content. Engines that support
  follow-up questions can answer questions about the previous description.
* **AI Agent** analyzes the screen and performs computer actions for a task.

OCR and image description support the following recognition sources:

* Navigator object
* Whole screen
* Foreground window
* Image or image file on the clipboard

AI Agent captures the full screen directly and does not use the selected
recognition source.

## Settings

All Vis Aware settings are available from `NVDA menu > Preferences > Vis Aware
settings...`.

The settings dialog contains these panels:

* **General**: result display, copying results to the clipboard, automatic
  reading, debug logging, recognition sources included when cycling, the Vis
  Aware mode, and the NVDACN account.
* **Automatic recognition**: recognition type, engine, prompt, model, model
  fetching, and the web-image screenshot preference.
* **OCR**, **Image description**, and **AI Agent**: the current engine, engine
  enablement, and settings provided by each engine.

You can disable engines you do not use. Disabled engines are skipped during
normal use and engine cycling. At least one engine must remain enabled for
each mode.

## Commands and gestures

The following commands have default gestures:

| Command | Default gesture |
| --- | --- |
| Cycle through recognition modes | `NVDA+alt+1` |
| Cycle through engines for the current mode | `NVDA+alt+2` |
| Cycle through recognition sources | `NVDA+alt+3` |
| Perform recognition using the current settings | `NVDA+alt+space` |
| Ask a follow-up question about the previous image description | `NVDA+alt+q` |

The following commands have no default gesture. Assign them in NVDA's **Input
Gestures** dialog:

* Describe the content of the current navigator object
* Describe images on the clipboard
* Ask a question before image description
* Recognize the content of the current navigator object using OCR
* Recognize text in images on the clipboard using OCR
* Show the previous recognition result
* Cancel the current recognition
* Cycle through automatic recognition modes: off, image description, and OCR

## Recognition results

Recognition results can be shown in an NVDA recognition result document,
announced directly, or shown in browse mode, depending on the settings. Browse
mode renders supported Markdown and mathematical formulas. Results can also be
copied to the clipboard.

When OCR results include coordinates, press `Enter` or `Space` on text in the
recognition result document to simulate a mouse click at the corresponding
location on the screen.

Vis Aware retains only the most recent recognition result during the current
NVDA session. For image description engines that support follow-up questions,
the follow-up command opens a multi-turn dialog. In that dialog, press
`Control+Enter` to send a question and `Escape` to cancel the request or close
the dialog. Use **View formatted content** to view the conversation in browse
mode, which is useful for mathematical formulas and Markdown content.

## AI Agent

AI Agent first asks what task you want to complete, starts with the foreground
window, and can operate across windows. It can click, type, press keys, scroll,
drag and drop, navigate, and wait. It can also ask you for information when
needed. Actions are not confirmed one by one, so monitor the session and stop
it when necessary.

Each AI Agent step sends a full-screen screenshot to the selected service. The
screenshot may include content from other windows. Avoid running AI Agent while
sensitive information is visible on the screen.

## Automatic recognition

Automatic recognition starts in the background when the system focus, browse
mode cursor, or navigator object moves to a supported graphic control. The
result is announced automatically and saved as the previous recognition
result. Follow-up questions are available when supported by the engine.

Automatic recognition is off by default. In the **Automatic recognition**
panel, choose OCR or image description, then choose the current engine or a
specific enabled engine. The prompt and model override that engine's settings
only for automatic recognition. Leave the prompt blank to use Vis Aware's
concise default prompt, limited to 20 to 30 characters and without Markdown.
Select **Use the model selected in engine settings** to use the engine's
regular model. When supported, use **Fetch models** to load available model
names.

The automatic recognition engine list includes only engines that support this
feature. When **Current engine** is selected and the current engine does not
support automatic recognition, automatic recognition pauses and resumes when
you select a supported engine.

For web graphics, Vis Aware normally uses the image URL and falls back to an
object screenshot when no URL is available. When **Prefer screenshots for web
images** is enabled, Vis Aware first recognizes the content shown on screen and
falls back to the URL if a screenshot cannot be captured.

---

## Supported engines

### Be My AI

Be My AI can describe an image and answer follow-up questions
about the same image. To use it, open the image description settings, choose
**Log in to Be My Eyes...**, and enter your Be My Eyes email address and
password. Vis Aware does not store your password.

This engine supports manual image descriptions and follow-up questions. It
does not support automatic recognition, custom prompts, or streaming. If you
cancel a follow-up after sending it, recognize the image again before asking
another question; otherwise, a delayed response may be associated with a later
question.

Images and questions are sent to Be My Eyes. Service availability, account
limits, and available features are controlled by Be My Eyes and may change.
Vis Aware is not responsible for account restrictions, service changes, data
loss, or other consequences of using the service. Follow Be My Eyes' applicable
terms and avoid sending sensitive information unless you accept the risks.

### Baimiao OCR / Formula Recognition

To use [Baimiao OCR](https://baimiaoapp.com) or Baimiao Formula Recognition,
open the OCR engine settings, choose **Log in to Baimiao...**, and enter your
Baimiao email address or mobile number and password. Both engines share the
same login session.

Vis Aware stores a fixed identifier for this device, along with the display
name or masked login identifier and membership level shown on the settings
button. These values are protected with Windows DPAPI; your password is never
stored. Formula Recognition returns both text and formulas. In browsable
results, formulas are presented in a screen-reader-friendly format.

Both engines use the [Baimiao web version](https://web.baimiaoapp.com/). Device
limits, quotas, and available features are determined by Baimiao and may vary
by account. Logging out in Vis Aware clears the login stored on this computer,
but does not guarantee that the device is signed out of the Baimiao account.
Sign out devices you no longer use in the Baimiao mobile app.

This integration uses a non-public Baimiao web interface. The interface may
change or become unavailable without notice. Use it at your own risk; Vis Aware
is not responsible for account restrictions, service changes, data loss, or
other consequences of using this interface. Follow Baimiao's applicable terms.

### Apple Vision Server

Apple Vision Server is a local-network OCR engine. Vis Aware sends the
selected image to the open-source iOS app [OCR Server](https://github.com/riddleling/iOS-OCR-Server),
which uses Apple Vision to recognize text and return text coordinates. No cloud
OCR account is required, but the computer and iPhone must be able to reach
each other over the same local network.

The current app requires iOS 18.4 or later. The oldest supported models are
iPhone XS, iPhone XS Max, iPhone XR, and iPhone SE (2nd generation).

To configure the engine:

1. Install [OCR Server from the App Store](https://apps.apple.com/us/app/ocr-server/id6749533041)
   on the iPhone.
2. Connect the iPhone and computer to the same local network, then open OCR
   Server. The server starts automatically and displays the address to use.
3. Open `NVDA menu > Preferences > Vis Aware settings... > OCR`, enable and
   select **Apple Vision Server**, and enter the address displayed by the
   app, such as `192.168.1.10:8000`.

Connections to OCR Server use unauthenticated, unencrypted HTTP. Use this
engine only on a trusted local network. On a public or untrusted network,
transmitted images and returned OCR results could be intercepted or modified.

Keep OCR Server open in the foreground and keep the iPhone screen awake while
using this engine. For continuous operation, follow the upstream project's
instructions for enabling iOS Guided Access. The upstream project also
documents app usage and the server API.

### Recommended offline setup: local models with Ollama

For an offline, self-hosted setup, we recommend using [Ollama](https://ollama.org/)
to run a vision-capable model locally. After the model is downloaded and the
API URL points to the local service, Vis Aware sends images to that service.

You can enter a full API URL or a host and port, such as `localhost:11434`.
The default API root is `http://localhost:11434/api`.

Use **Fetch models** to load model names and then choose a model. If no model
is selected, Vis Aware uses the first model returned by Ollama.

The API key is optional. Ollama engines require a vision-capable model, such
as Gemma 4. Ollama provides screen coordinates for OCR only when the model returns
valid structured OCR data.

### Other engines

* PaddleOCR supports an AI Studio hosted task API, an AI Studio deployed
  service, or a self-hosted PaddleOCR service. Choose PaddleOCR or PaddleOCR-VL
  as the service type in the engine settings.
* Kimi engines use an OpenAI-compatible protocol. For the Kimi Open Platform,
  enter a key created at the [Kimi Open Platform](https://platform.kimi.com/console/api-keys).
  For the international platform, enter a key created at the [international
  Kimi platform](https://platform.kimi.ai/console/api-keys). Do not mix an
  endpoint with a key from another service. The default model is Kimi K3;
  available models and reasoning options vary by endpoint and model family.
* DeepSeek engines use an OpenAI-compatible protocol. Set the Base URL to
  `https://api.deepseek.com`, create a key in the [DeepSeek API console](https://platform.deepseek.com/api_keys),
  and enter it in the engine settings. The default vision model is
  `deepseek-flash`, used by both the image description and AI Agent engines.

## Data and credentials

Recognition sends the selected image and, when applicable, the prompt to the
service configured for the selected engine. Each AI Agent step sends a
full-screen screenshot to the selected service, and the screenshot may include
other windows. Review the service's data policy and avoid sending sensitive
content.

On supported systems with NVDA 2026.3 or later, manual and automatic screen
recognition can capture the original screen even when Screen Curtain or NVDA's
built-in Magnifier is enabled.

Some sensitive login sessions are protected with Windows DPAPI. Other saved
API keys are stored in plain text in the NVDA configuration. Keep this in mind
before creating or sharing a portable copy of NVDA.

## License

This project is licensed under the GNU General Public License version 2. See
`COPYING.txt` for details.
