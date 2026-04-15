from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Callable


TAB_CSS = """
:root {
  --bg-base: #ffffff;
  --bg-surface: #f8f8f8;
  --bg-elevated: #f0f0f0;
  --bg-hover: #ebebeb;
  --border: #e0e0e0;
  --border-subtle: #eeeeee;
  --text-primary: #111111;
  --text-secondary: #555555;
  --text-muted: #999999;
  --accent-orange: #e05a1e;
  --accent-green: #1a7a3e;
  --accent-blue: #1a5fb4;
  --accent-purple: #6b3fa0;
  --accent-yellow: #b45309;
  --accent-red: #c0392b;
  --tab-active-border: #111111;
  --tab-active-text: #111111;
  --tab-inactive-text: #888888;
  --cursor-color: #111111;
  --timeline-bg: #fafafa;
  --timeline-rule: #e8e8e8;
  --font-mono: 'JetBrains Mono', 'Fira Mono', 'Courier New', monospace;
  --font-ui: 'IBM Plex Sans', 'Helvetica Neue', sans-serif;
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 16px;
  --space-lg: 24px;
  --space-xl: 40px;
  --radius-sm: 3px;
  --radius-md: 6px;
  --timeline-label-width: 360px;
  --timeline-gutter: 32px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg-base);
  color: var(--text-primary);
  font-family: var(--font-ui);
  font-size: 14px;
  line-height: 1.6;
}
.page {
  max-width: 1400px;
  margin: 0 auto;
  padding: var(--space-lg);
}
.header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
}
.header-copy { min-width: 0; }
.title {
  margin: 0;
  font-size: 28px;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.subtitle {
  margin-top: var(--space-xs);
  color: var(--text-secondary);
}
.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}
.truncate {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.break-word {
  word-break: break-word;
  overflow-wrap: anywhere;
}
.tabs {
  display: flex;
  gap: var(--space-sm);
  border-bottom: 1px solid var(--border);
  margin-bottom: var(--space-lg);
  overflow-x: auto;
  padding-bottom: 1px;
}
.tab-button {
  appearance: none;
  background: transparent;
  border: 0;
  border-bottom: 2px solid transparent;
  color: var(--tab-inactive-text);
  font: inherit;
  padding: var(--space-sm) var(--space-sm) 12px;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
}
.tab-button.active {
  color: var(--tab-active-text);
  border-bottom-color: var(--tab-active-border);
}
.tab-panel {
  display: none;
  min-width: 0;
}
.tab-panel.active {
  display: block;
}
.card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: var(--space-md);
  min-width: 0;
  overflow: hidden;
}
.card:hover {
  background: var(--bg-elevated);
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
}
.summary-value {
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.summary-label {
  color: var(--text-secondary);
}
.kv-grid {
  display: grid;
  grid-template-columns: minmax(0, 180px) minmax(0, 1fr);
  gap: var(--space-sm) var(--space-md);
}
.kv-label {
  color: var(--text-secondary);
}
.grid-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-md);
}
.grid-2 {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
  gap: var(--space-lg);
}
.grid-2-agent-b {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr);
  gap: var(--space-lg);
}
.stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
  min-width: 0;
}
.badge {
  display: inline-block;
  padding: 2px 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  flex-shrink: 0;
}
.badge-sfx          { background: #fde8dc; color: var(--accent-orange); }
.badge-ambience     { background: #d8e8f8; color: var(--accent-blue); }
.badge-hard_effect  { background: #fde8dc; color: var(--accent-orange); }
.badge-foley        { background: #d9f0e3; color: var(--accent-green); }
.badge-background   { background: #d8e8f8; color: var(--accent-blue); }
.badge-electronic   { background: #ede0f5; color: var(--accent-purple); }
.badge-warning      { background: #fef3c7; color: var(--accent-yellow); }
.badge-error        { background: #fde8e8; color: var(--accent-red); }
.badge-success      { background: #d9f0e3; color: var(--accent-green); }
.badge-info         { background: #efefef; color: var(--text-secondary); }
.badge-audible      { background: #d9f0e3; color: var(--accent-green); }
.badge-likely_audible { background: #fde8dc; color: var(--accent-orange); }
.badge-visual_only  { background: #efefef; color: var(--text-secondary); }
.badge-inactive     { background: #efefef; color: var(--text-muted); }
video {
  width: 100%;
  display: block;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: #000;
  max-width: 100%;
  height: auto;
}
.video-frame {
  max-width: 720px;
  margin: 0 auto var(--space-lg);
}
.video-placeholder {
  display: grid;
  place-items: center;
  width: 100%;
  min-height: 220px;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-surface);
  color: var(--text-muted);
  text-transform: lowercase;
}
table {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
  font-size: 13px;
}
th, td {
  padding: 8px 10px;
  text-align: left;
  border-bottom: 1px solid var(--border-subtle);
  min-width: 0;
  vertical-align: top;
}
td > * { min-width: 0; }
th {
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  background: var(--bg-surface);
  white-space: nowrap;
}
td {
  white-space: normal;
  overflow: visible;
  text-overflow: clip;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.table-wrap {
  overflow-x: auto;
  min-width: 0;
}
.table-wrap .truncate {
  white-space: normal;
  overflow: visible;
  text-overflow: clip;
}
.table-wrap .mono {
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-sm);
  margin-bottom: var(--space-sm);
}
.section-title h2,
.section-title h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.accordion-item + .accordion-item {
  margin-top: var(--space-md);
}
.accordion-trigger {
  width: 100%;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: var(--space-md);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-md);
  cursor: pointer;
  text-align: left;
}
.accordion-trigger:hover {
  background: var(--bg-elevated);
}
.accordion-meta {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  min-width: 0;
}
.accordion-content {
  display: none;
  overflow: hidden;
  padding-top: var(--space-md);
}
.accordion-content.open {
  display: block;
}
.action-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
  min-width: 0;
}
.action-card {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: #fff;
  padding: 12px;
  min-width: 0;
}
.action-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-sm);
  align-items: center;
  margin-bottom: var(--space-sm);
  min-width: 0;
}
.muted {
  color: var(--text-secondary);
}
.timeline-wrap {
  position: relative;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--timeline-bg);
}
.timeline-overlay {
  position: absolute;
  top: 0;
  bottom: 0;
  left: calc(var(--timeline-label-width) + var(--timeline-gutter));
  right: 0;
  pointer-events: none;
  z-index: 3;
}
.timeline-cursor {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--cursor-color);
  left: 0;
}
.timeline-row,
.timeline-ruler-row {
  display: flex;
  min-width: 0;
}
.timeline-label {
  width: var(--timeline-label-width);
  flex-shrink: 0;
  padding: 10px 12px;
  border-right: 1px solid var(--border);
  background: #fff;
  min-width: 0;
  overflow: visible;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.timeline-label .mono {
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.timeline-lane,
.timeline-ruler-lane {
  flex: 1;
  min-width: 0;
  position: relative;
  overflow: hidden;
}
.timeline-lane::before,
.timeline-ruler-lane::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: var(--timeline-gutter);
  background: linear-gradient(to right, rgba(255,255,255,0.96), rgba(255,255,255,0.65));
  border-right: 1px solid var(--border);
  z-index: 1;
  pointer-events: none;
}
.timeline-lane-inner,
.timeline-ruler-inner {
  position: relative;
  height: 100%;
  width: calc(100% - var(--timeline-gutter));
  margin-left: var(--timeline-gutter);
  overflow: hidden;
}
.timeline-ruler-row {
  border-bottom: 1px solid var(--border);
}
.timeline-ruler-label {
  width: var(--timeline-label-width);
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  background: #fff;
}
.timeline-ruler-lane {
  height: 34px;
  background: var(--timeline-bg);
}
.timeline-row + .timeline-row {
  border-top: 1px solid var(--border-subtle);
}
.timeline-lane {
  min-height: 44px;
}
.timeline-rule,
.timeline-major-rule {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
}
.timeline-rule {
  background: var(--timeline-rule);
}
.timeline-major-rule {
  background: var(--border);
}
.timeline-major-label {
  position: absolute;
  top: 4px;
  transform: translateX(-50%);
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-muted);
  white-space: nowrap;
}
.timeline-major-label.timeline-start-label {
  transform: none;
}
.timeline-event-onset,
.timeline-event-continuous {
  position: absolute;
  border-radius: var(--radius-sm);
  z-index: 2;
}
.timeline-event-onset {
  top: 15%;
  bottom: 15%;
  width: 3px;
  min-width: 3px;
  transform: translateX(-50%);
}
.timeline-event-continuous {
  top: 25%;
  bottom: 25%;
  min-width: 3px;
  border: 1px solid currentColor;
  opacity: 0.35;
}
.interaction-color-sfx { color: var(--accent-orange); background: rgba(224, 90, 30, 0.18); }
.interaction-color-ambience { color: var(--accent-blue); background: rgba(26, 95, 180, 0.18); }
.interaction-color-hard_effect { color: var(--accent-orange); background: rgba(224, 90, 30, 0.18); }
.interaction-color-foley { color: var(--accent-green); background: rgba(26, 122, 62, 0.18); }
.interaction-color-background { color: var(--accent-blue); background: rgba(26, 95, 180, 0.18); }
.interaction-color-electronic { color: var(--accent-purple); background: rgba(107, 63, 160, 0.18); }
.empty-state {
  color: var(--text-secondary);
  padding: var(--space-md);
  border: 1px dashed var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-surface);
}
pre {
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  margin: 0;
}
@media (max-width: 980px) {
  .grid-2,
  .grid-2-agent-b {
    grid-template-columns: minmax(0, 1fr);
  }
  .page { padding: var(--space-md); }
}
"""


TAB_JS = """
function initTabs() {
  const buttons = Array.from(document.querySelectorAll('[data-tab-target]'));
  const panels = Array.from(document.querySelectorAll('[data-tab-panel]'));
  if (!buttons.length) return;
  const activate = (target) => {
    buttons.forEach((button) => {
      button.classList.toggle('active', button.dataset.tabTarget === target);
    });
    panels.forEach((panel) => {
      panel.classList.toggle('active', panel.dataset.tabPanel === target);
    });
  };
  buttons.forEach((button) => {
    button.addEventListener('click', () => activate(button.dataset.tabTarget));
  });
  activate(buttons[0].dataset.tabTarget);
}

function initAccordions() {
  const triggers = document.querySelectorAll('[data-accordion-target]');
  triggers.forEach((trigger) => {
    trigger.addEventListener('click', () => {
      const content = document.getElementById(trigger.dataset.accordionTarget);
      if (!content) return;
      content.classList.toggle('open');
    });
  });
}

function toPercent(t, duration) {
  if (!duration || duration <= 0 || Number.isNaN(duration)) return 0;
  return Math.max(0, Math.min(100, (t / duration) * 100));
}

function initTimelineSync() {
  const wrappers = document.querySelectorAll('.timeline-wrap[data-video-id]');
  wrappers.forEach((wrapper) => {
    const video = document.getElementById(wrapper.dataset.videoId);
    const cursor = wrapper.querySelector('.timeline-cursor');
    if (!video || !cursor) return;
    video.addEventListener('timeupdate', () => {
      if (!video.duration || Number.isNaN(video.duration)) return;
      const pct = toPercent(video.currentTime, video.duration);
      cursor.style.left = `${pct}%`;
    });
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initAccordions();
  initTimelineSync();
});
"""


def safe_load_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def safe_text(value: object, fallback: str = "—") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    return html.escape(text)


def resolve_video_src(path: str | None, report_html_path: Path) -> str:
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.exists():
        return ""
    try:
        return os.path.relpath(file_path.resolve(), report_html_path.parent.resolve())
    except Exception:
        return str(file_path.resolve())


def render_stage_not_found(stage_label: str) -> str:
    return (
        "<div class='empty-state'>"
        f"{safe_text(stage_label)} artifact not found"
        "</div>"
    )


def render_video_or_placeholder(
    src: str,
    *,
    controls: bool = True,
    muted: bool = True,
    preload: str = "metadata",
    element_id: str | None = None,
    extra_class: str = "",
) -> str:
    if not src:
        return "<div class='video-placeholder'>video not available</div>"
    attrs = []
    if controls:
        attrs.append("controls")
    if muted:
        attrs.append("muted")
    attrs.append(f'preload="{html.escape(preload)}"')
    if element_id:
        attrs.append(f'id="{html.escape(element_id)}"')
    classes = f" {extra_class.strip()}" if extra_class.strip() else ""
    return f"<video class='{classes.strip()}' {' '.join(attrs)} src=\"{html.escape(src)}\"></video>"


def render_badge(value: str, *, kind: str = "interaction") -> str:
    normalized = str(value).strip()
    if kind == "interaction":
        class_name = f"badge-{normalized}"
    elif kind == "severity":
        class_name = f"badge-{normalized.lower()}"
    elif kind == "audibility":
        class_name = f"badge-{normalized}"
    else:
        class_name = "badge-info"
    return f"<span class='badge {html.escape(class_name)}'>{safe_text(normalized)}</span>"


def render_summary_card(label: str, value: object) -> str:
    return (
        "<div class='card'>"
        f"<div class='summary-value break-word'>{safe_text(value)}</div>"
        f"<div class='summary-label'>{safe_text(label)}</div>"
        "</div>"
    )


def render_tab_shell(tab_id: str, content: str) -> str:
    return f"<section class='tab-panel' data-tab-panel='{html.escape(tab_id)}'>{content}</section>"


def wrap_report_page(*, run_id: str, title: str, tabs: list[tuple[str, str, str]]) -> str:
    tab_buttons = "".join(
        f"<button class='tab-button' type='button' data-tab-target='{html.escape(tab_id)}'>{safe_text(label)}</button>"
        for tab_id, label, _ in tabs
    )
    tab_panels = "".join(render_tab_shell(tab_id, content) for tab_id, _, content in tabs)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_text(title)}</title>
  <style>{TAB_CSS}</style>
</head>
<body>
  <div class="page">
    <header class="header">
      <div class="header-copy">
        <h1 class="title">{safe_text(title)}</h1>
        <div class="subtitle">run_id: <span class="mono break-word">{safe_text(run_id)}</span></div>
      </div>
    </header>
    <nav class="tabs">{tab_buttons}</nav>
    {tab_panels}
  </div>
  <script>{TAB_JS}</script>
</body>
</html>"""


def stage_output(run_dir: Path, stage_dir: str) -> dict | list | None:
    return safe_load_json(run_dir / stage_dir / "output.json")


def stage_warning_envelope(run_dir: Path, stage_dir: str) -> dict | None:
    payload = safe_load_json(run_dir / stage_dir / "warnings.json")
    return payload if isinstance(payload, dict) else None


def render_warning_table(warnings: list[dict]) -> str:
    if not warnings:
        return ""
    rows = "".join(
        "<tr>"
        f"<td>{render_badge(str(item.get('severity', 'warning')), kind='severity')}</td>"
        f"<td class='mono truncate'>{safe_text(item.get('code'))}</td>"
        f"<td class='break-word'>{safe_text(item.get('message'))}</td>"
        f"<td><pre>{safe_text(json.dumps(item.get('context', {}), ensure_ascii=False, indent=2))}</pre></td>"
        "</tr>"
        for item in warnings
    )
    return (
        "<div class='card'>"
        "<div class='section-title'><h3>Warnings</h3></div>"
        "<div class='table-wrap'><table>"
        "<thead><tr><th>Severity</th><th>Code</th><th>Message</th><th>Context</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></div>"
    )


def load_stage_warnings(run_dir: Path, stage_dir: str) -> list[dict]:
    envelope = stage_warning_envelope(run_dir, stage_dir)
    if not isinstance(envelope, dict):
        return []
    warnings = envelope.get("warnings", [])
    return warnings if isinstance(warnings, list) else []


def render_tab_with_isolation(stage_label: str, renderer: Callable[[], str | None]) -> str:
    try:
        content = renderer()
    except Exception as exc:
        return (
            "<div class='empty-state'>"
            f"{safe_text(stage_label)} failed to render: {safe_text(exc)}"
            "</div>"
        )
    return content or render_stage_not_found(stage_label)
