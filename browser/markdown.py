from __future__ import annotations

import json
import re
from typing import Any

from parsers.page_markdown import convert_page_to_markdown


_SNAPSHOT_JS = r"""
const root = document.body ? document.body.cloneNode(true) : document.documentElement.cloneNode(true);
const noiseSelectors = [
  'script',
  'style',
  'noscript',
  'template',
  'svg',
  'path',
  'meta',
  'link',
  'iframe',
  'object',
  'embed'
];
for (const selector of noiseSelectors) {
  root.querySelectorAll(selector).forEach(node => node.remove());
}
root.querySelectorAll('img').forEach(img => {
  const src = (img.getAttribute('src') || '').trim();
  const alt = (img.getAttribute('alt') || '').trim();
  const width = parseInt(img.getAttribute('width') || img.width || '0', 10) || 0;
  const height = parseInt(img.getAttribute('height') || img.height || '0', 10) || 0;
  const cls = (img.className || '').toString().toLowerCase();
  if (
    src.startsWith('data:image/gif') ||
    src.startsWith('data:image/png') ||
    src.includes('collect?') ||
    src.includes('setuid?') ||
    src.includes('pixel') ||
    src.includes('tracking') ||
    (!alt && width <= 1 && height <= 1) ||
    (!alt && cls.includes('lazy-image') && (width <= 1 || height <= 1))
  ) {
    img.remove();
  }
});
root.querySelectorAll('input, button, select, textarea, [role="switch"], [role="checkbox"], [role="radio"], [aria-pressed], [aria-expanded], [aria-checked]').forEach(node => {
  try {
    if (node.tagName === 'INPUT') {
      node.setAttribute('data-codex-checked', node.checked ? 'true' : 'false');
      node.setAttribute('data-codex-indeterminate', node.indeterminate ? 'true' : 'false');
      node.setAttribute('data-codex-input-type', (node.getAttribute('type') || '').toLowerCase());
    }
    if (node.tagName === 'OPTION') {
      node.setAttribute('data-codex-selected', node.selected ? 'true' : 'false');
    }
    if (node.tagName === 'SELECT') {
      const selected = node.options && node.selectedIndex >= 0 ? node.options[node.selectedIndex] : null;
      if (selected) {
        node.setAttribute('data-codex-selected-text', (selected.innerText || selected.textContent || '').trim());
      }
    }
    if (node.hasAttribute('aria-pressed')) {
      node.setAttribute('data-codex-pressed', node.getAttribute('aria-pressed') || 'false');
    }
    if (node.hasAttribute('aria-expanded')) {
      node.setAttribute('data-codex-expanded', node.getAttribute('aria-expanded') || 'false');
    }
    if (node.hasAttribute('aria-checked')) {
      node.setAttribute('data-codex-checked', node.getAttribute('aria-checked') || 'false');
    }
    if (node.hasAttribute('aria-current')) {
      node.setAttribute('data-codex-current', node.getAttribute('aria-current') || '');
    }
  } catch (e) {}
});
return root.outerHTML;
"""


def _snapshot_html(driver) -> str:
    executor = getattr(driver, "execute_script", None)
    if callable(executor):
        try:
            snapshot = executor(_SNAPSHOT_JS)
            if isinstance(snapshot, str) and snapshot.strip():
                return snapshot
        except Exception:
            pass
    return getattr(driver, "page_source", "") or ""


def _is_json_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped[0] not in "{[" or stripped[-1] not in "}]":
        return False
    try:
        json.loads(stripped)
    except Exception:
        return False
    return True


def _remove_json_lines(markdown: str) -> str:
    lines: list[str] = []
    previous_blank = False
    for raw_line in markdown.splitlines():
        if _is_json_line(raw_line):
            continue
        line = raw_line.rstrip()
        if not line.strip():
            if previous_blank:
                continue
            lines.append("")
            previous_blank = True
            continue
        previous_blank = False
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _remove_noise_lines(markdown: str) -> str:
    noisy_patterns = (
        r"^PMBR-\d+$",
        r"^HUED-\d+$",
        r"^EMBER_[A-Z0-9_]+$",
        r"^graphql-script-injection$",
        r"^urn:li:[^ ]+$",
        r"^EMBER_CLI_FASTBOOT_BODY$",
        r"^Status is online$",
    )
    lines: list[str] = []
    previous_blank = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if previous_blank:
                continue
            lines.append("")
            previous_blank = True
            continue
        if any(re.match(pattern, stripped) for pattern in noisy_patterns):
            continue
        if stripped.startswith("![image](data:image"):
            continue
        if stripped.startswith("data:image/"):
            continue
        previous_blank = False
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _remove_markdown_links(markdown: str) -> str:
    link_pattern = re.compile(r"(?<!\!)\[(?P<text>[^\]]+)\]\((?P<url>[^)]+)\)")
    nested_wrapper_pattern = re.compile(r"\[(?P<label>[^\]]+)\]\(\[[^\]]+\]\([^)]+\)\)")

    cleaned_lines: list[str] = []
    for raw_line in markdown.splitlines():
        stripped = raw_line.strip()
        if not stripped or not link_pattern.search(stripped):
            cleaned_lines.append(raw_line)
            continue

        flattened = nested_wrapper_pattern.sub(lambda match: f"[{match.group('label')}]", raw_line)
        matches = list(link_pattern.finditer(flattened))
        if len(matches) == 1:
            match = matches[0]
            before = flattened[: match.start()].strip()
            after = flattened[match.end() :].strip()
            if not before and (not after or re.fullmatch(r"(\[\[i\d+\]\]|\(img\d+\)|\s)+", after)):
                cleaned_lines.append(flattened)
                continue

        cleaned_lines.append(link_pattern.sub(lambda match: match.group("text") or "", flattened))
    return "\n".join(cleaned_lines)


def output_markdown(
    driver,
    remove_json: bool = True,
    remove_links: bool = True,
) -> tuple[str, dict[str, Any]]:
    url = getattr(driver, "current_url", "") or ""
    page_source = _snapshot_html(driver)
    markdown, dev = convert_page_to_markdown(page_source, url)
    markdown = _remove_noise_lines(markdown)
    if remove_links:
        markdown = _remove_markdown_links(markdown)
    if remove_json:
        markdown = _remove_json_lines(markdown)
    return markdown, dev
