from __future__ import annotations

import importlib.util
import builtins
from pathlib import Path
import sys
import types
import unittest


def _install_module_stubs() -> None:
	builtins._ = lambda value: value

	addonHandlerModule = types.ModuleType("addonHandler")
	addonHandlerModule.initTranslation = lambda: None
	sys.modules["addonHandler"] = addonHandlerModule

	for moduleName in ("api", "gui", "mouseHandler", "wx"):
		sys.modules[moduleName] = types.ModuleType(moduleName)

	keyboardHandlerModule = types.ModuleType("keyboardHandler")
	keyboardHandlerModule.KeyboardInputGesture = object
	sys.modules["keyboardHandler"] = keyboardHandlerModule

	logHandlerModule = types.ModuleType("logHandler")
	logHandlerModule.log = type(
		"Log",
		(),
		{
			"debug": staticmethod(lambda *args, **kwargs: None),
			"debugWarning": staticmethod(lambda *args, **kwargs: None),
			"error": staticmethod(lambda *args, **kwargs: None),
			"info": staticmethod(lambda *args, **kwargs: None),
			"warning": staticmethod(lambda *args, **kwargs: None),
		},
	)()
	sys.modules["logHandler"] = logHandlerModule

	winUserModule = types.ModuleType("winUser")
	winUserModule.WHEEL_DELTA = 120
	sys.modules["winUser"] = winUserModule


def load_actions_module():
	_install_module_stubs()
	modulePath = (
		Path(__file__).resolve().parents[1] / "addon" / "globalPlugins" / "visAware" / "agent" / "actions.py"
	)
	spec = importlib.util.spec_from_file_location("addon.globalPlugins.visAware.agent.actions", modulePath)
	if spec is None or spec.loader is None:
		raise RuntimeError("Failed to load actions.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


class AgentActionsTestCase(unittest.TestCase):
	def test_screenshot_prompt_context_includes_window_title(self) -> None:
		module = load_actions_module()
		screenshot = module.Screenshot(
			imageBase64="",
			mimeType="image/jpeg",
			width=1536,
			height=864,
			screenLeft=0,
			screenTop=0,
			screenWidth=1920,
			screenHeight=1080,
			digest="digest",
			window=module.BoundWindow(
				hwnd=1,
				processID=1,
				appName="test",
				title="  Settings   - Example App  ",
				left=100,
				top=50,
				width=800,
				height=600,
			),
		)

		context = module.formatScreenshotPromptContext(screenshot)

		self.assertIn("Foreground window title: 'Settings - Example App'.", context)
		self.assertIn("Screenshot is full screen (1920x1080).", context)


if __name__ == "__main__":
	unittest.main()
