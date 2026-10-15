from pathlib import Path
import unittest


TEMPLATE = Path(__file__).resolve().parents[1] / "web_panel" / "templates" / "server.html"


class ConsoleScrollTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = TEMPLATE.read_text(encoding="utf-8")

    def test_polling_and_websocket_both_use_scroll_aware_renderer(self):
        self.assertEqual(self.source.count("renderConsoleLogs(item.logs);"), 2)
        self.assertNotIn(
            "document.getElementById('logs').scrollTop=document.getElementById('logs').scrollHeight;",
            self.source,
        )

    def test_manual_scroll_disables_follow_until_viewer_returns_to_bottom(self):
        self.assertIn("let consoleFollow=true;", self.source)
        self.assertIn("function isConsoleNearBottom(box)", self.source)
        self.assertIn(
            "addEventListener('scroll',event=>{consoleFollow=isConsoleNearBottom(event.currentTarget)})",
            self.source,
        )
        self.assertIn("if(consoleFollow)box.scrollTop=box.scrollHeight;", self.source)
        self.assertIn("Math.min(previousTop,Math.max(0,box.scrollHeight-box.clientHeight))", self.source)


if __name__ == "__main__":
    unittest.main()
