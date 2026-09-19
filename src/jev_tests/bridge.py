"""Backend-swappable browser implementation for Jev Ultrafast.

Jev selects an observed integer node id. The model never supplies selectors.
The bridge validates that exact observed node, stamps a short-lived code-owned
attribute, then asks Playwriter or Browser Relay to execute the native action.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

import jev_ultrafast
from jev_ultrafast.browser import StalePage

READ_STATE = Path(jev_ultrafast.__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"


def _fingerprint(state: dict[str, Any]) -> str:
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def _decode_jsonish(raw: str) -> Any:
    s = (raw or "").strip()
    marker = "__JEV_JSON__"
    if marker in s:
        s = s.rsplit(marker, 1)[1].strip()
    for _ in range(3):
        try:
            value = json.loads(s)
        except Exception:
            break
        if isinstance(value, str):
            s = value.strip()
            continue
        return value
    matches = re.findall(r'(\{.*\}|\[.*\]|"(?:[^"\\]|\\.)*")', s, re.S)
    if matches:
        try:
            return json.loads(matches[-1])
        except Exception:
            pass
    return s


class Transport(Protocol):
    def navigate(self, url: str) -> None: ...
    def evaluate(self, expression: str) -> Any: ...
    def click(self, selector: str) -> None: ...
    def fill(self, selector: str, text: str) -> None: ...
    def key(self, key: str) -> None: ...
    def scroll(self, delta: int) -> None: ...
    def close(self) -> None: ...


class BrowserRelayTransport:
    """Browser Relay CLI transport against an attached Chrome tab."""

    def __init__(self, url: str):
        self.bin = os.environ.get("BROWSER_RELAY_BIN", "browser-relay")
        self.tab = os.environ.get("BROWSER_RELAY_TAB") or self._choose_tab(url)
        self.navigate(url)

    def _run(self, *args: str, timeout: int = 90) -> str:
        p = subprocess.run(
            [self.bin, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode:
            raise RuntimeError(out.strip() or f"browser-relay exited {p.returncode}")
        return out.strip()

    def _choose_tab(self, url: str) -> str:
        host = url.split("/")[2].lower() if "://" in url else ""
        out = self._run("tabs")
        rows = [line for line in out.splitlines() if "\t" in line]
        for line in rows:
            if host and host in line.lower():
                return line.split("\t", 1)[0].strip()
        if rows:
            return rows[0].split("\t", 1)[0].strip()
        raise RuntimeError("Browser Relay has no attached tab; set BROWSER_RELAY_TAB")

    def navigate(self, url: str) -> None:
        self._run("navigate", url, "--tab", self.tab)

    def evaluate(self, expression: str) -> Any:
        wrapped = (
            "JSON.stringify({tag:'__JEV__',value:(() => { return ("
            + expression
            + "); })()})"
        )
        out = self._run("eval", wrapped, "--tab", self.tab)
        value = _decode_jsonish(out)
        if isinstance(value, dict) and value.get("tag") == "__JEV__":
            return value.get("value")
        return value

    def click(self, selector: str) -> None:
        self._run("click", selector, "--tab", self.tab)

    def fill(self, selector: str, text: str) -> None:
        self._run("type", text, "--selector", selector, "--clear", "--tab", self.tab)

    def key(self, key: str) -> None:
        self._run("key", key, "--tab", self.tab)

    def scroll(self, delta: int) -> None:
        direction = "down" if delta >= 0 else "up"
        self._run("scroll", direction, "--amount", str(abs(int(delta))), "--tab", self.tab)

    def close(self) -> None:
        pass


class PlaywriterTransport:
    """Playwriter CLI transport using an existing stateful session."""

    def __init__(self, url: str):
        self.bin = os.environ.get("PLAYWRITER_BIN", "playwriter")
        self.session = os.environ.get("PLAYWRITER_SESSION", "1")
        self.navigate(url)

    def _run(self, code: str, timeout: int = 90) -> str:
        p = subprocess.run(
            [self.bin, "-s", self.session, "-e", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode:
            raise RuntimeError(out.strip() or f"playwriter exited {p.returncode}")
        return out.strip()

    def navigate(self, url: str) -> None:
        self._run(f"await page.goto({json.dumps(url)})")

    def evaluate(self, expression: str) -> Any:
        code = (
            "const __v = await page.evaluate((source) => (0, eval)(source), "
            + json.dumps(expression)
            + "); console.log('__JEV_JSON__' + JSON.stringify(__v))"
        )
        return _decode_jsonish(self._run(code))

    def click(self, selector: str) -> None:
        self._run(f"await page.locator({json.dumps(selector)}).click()")

    def fill(self, selector: str, text: str) -> None:
        self._run(f"await page.locator({json.dumps(selector)}).fill({json.dumps(text)})")

    def key(self, key: str) -> None:
        self._run(f"await page.keyboard.press({json.dumps(key)})")

    def scroll(self, delta: int) -> None:
        self._run(f"await page.mouse.wheel(0, {int(delta)})")

    def close(self) -> None:
        pass


def _make_transport(url: str) -> Transport:
    backend = os.environ.get("JEV_BROWSER_BACKEND", "browser-relay").strip().lower()
    if backend == "browser-relay":
        return BrowserRelayTransport(url)
    if backend == "playwriter":
        return PlaywriterTransport(url)
    raise ValueError(f"Unknown JEV_BROWSER_BACKEND={backend!r}")


class BridgeBrowser:
    """Drop-in Browser for jev_ultrafast.agent.Agent."""

    def __init__(self, url: str):
        self.transport = _make_transport(url)
        self.after_input: dict[str, Any] | None = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if self.evaluate("document.readyState") == "complete":
                    break
            except Exception:
                pass
            time.sleep(0.05)

    def evaluate(self, expression: str) -> Any:
        return self.transport.evaluate(expression)

    def observe(self, screenshot: bool = False) -> dict[str, Any]:
        if screenshot:
            raise NotImplementedError("Fast-path runs use structured state only")
        if self.after_input:
            action, self.after_input = self.after_input, None
            time.sleep(0.2 if action.get("kind") == "fill" else 0.05)
        for attempt in range(10):
            try:
                info = self.evaluate(READ_STATE)
                if info is None:
                    raise StalePage("Document is navigating")
                info["fingerprint"] = _fingerprint(info)
                return info
            except Exception:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

    def fresh(self, page: dict[str, Any], action: dict[str, Any] | None = None) -> bool:
        if action is not None and action.get("kind") in {"click", "select"}:
            node = action.get("node")
            if not isinstance(node, int):
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def _stamp_target(self, action: dict[str, Any]) -> str:
        node = action.get("node")
        if not isinstance(node, int):
            raise ValueError("Invalid observed node")
        token = "jev-" + uuid.uuid4().hex
        attr = "data-jev-fast-target"
        result = self.evaluate(
            f"""(() => {{
              const e=window.__jevFast?.nodes.get({node});
              if (!e?.isConnected || e.matches(':disabled') ||
                  e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({{checkOpacity:true,checkVisibilityCSS:true}})) return null;
              const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              if (!e.contains(document.elementFromPoint(x,y))) return null;
              document.querySelectorAll('[{attr}]').forEach(n=>n.removeAttribute('{attr}'));
              e.setAttribute('{attr}', {json.dumps(token)});
              return true;
            }})()"""
        )
        if result is not True:
            raise StalePage("Target changed or is covered. Observe again.")
        return f'[{attr}="{token}"]'

    def act(
        self,
        action: dict[str, Any],
        page: dict[str, Any],
        text: str | None = None,
    ) -> dict[str, Any]:
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")

        kind = action["kind"]
        if kind == "wait":
            time.sleep(0.1)
        elif kind == "scroll":
            self.transport.scroll(int(action["delta"]))
        elif kind == "select":
            node = action.get("node")
            if not isinstance(node, int):
                raise ValueError("Invalid observed node")
            value = action.get("value")
            ok = self.evaluate(
                f"""(() => {{
                  const e=window.__jevFast?.nodes.get({node});
                  if (!e?.isConnected || e.tagName!=='SELECT') return false;
                  const option=[...e.options].find(o=>o.value==={json.dumps(value)} &&
                    !o.disabled && !o.closest('optgroup[disabled]'));
                  if (!option) return false;
                  e.value=option.value;
                  e.dispatchEvent(new Event('input',{{bubbles:true}}));
                  e.dispatchEvent(new Event('change',{{bubbles:true}}));
                  return true;
                }})()"""
            )
            if ok is not True:
                raise StalePage("Dropdown execution was not confirmed")
        else:
            selector = self._stamp_target(action)
            if kind == "click":
                self.transport.click(selector)
            elif kind == "fill":
                if text is None:
                    raise ValueError("fill requires text")
                self.transport.fill(selector, text)
                if os.environ.get("JEV_FILL_ENTER") == "1":
                    self.transport.key("Enter")
            else:
                raise ValueError(f"Unsupported Jev action kind: {kind}")

        self.after_input = action if kind != "wait" else None
        return {"executed": action["id"]}

    def close(self) -> None:
        self.transport.close()
