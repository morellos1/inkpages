"""Local review UI for the directory: browse artists, inspect evidence,
work the review queue, suppress/unsuppress.

Usage: uv run python -m inkpages.review_ui   (then open http://127.0.0.1:8322)
Local admin tooling — binds to 127.0.0.1 only.
"""
import json
import os
import re
import secrets
import subprocess
import sys
import time
from collections import Counter

from flask import Flask, abort, redirect, render_template, request, url_for
from jinja2 import DictLoader
from markupsafe import Markup
from psycopg.rows import dict_row

from . import db

PORT = int(os.environ.get("PORT", "8322"))

TEMPLATES = {
"base.html": """<!doctype html><html><head><meta charset="utf-8">
<title>inkpages review</title>
<style>
  body { font: 15px/1.5 -apple-system, system-ui, sans-serif; margin: 0; color: #1a1a1a; background: #fafafa; }
  header { background: #14213d; color: #fff; padding: .7rem 1.2rem; display: flex; gap: 1.4rem; align-items: baseline; }
  header a { color: #cdd7ee; text-decoration: none; } header a:hover { color: #fff; }
  header .brand { font-weight: 700; color: #fff; }
  header .pill { background: #fca311; color: #14213d; border-radius: 9px; padding: 0 .5em; font-size: .82em; font-weight: 700; }
  main { max-width: 1400px; margin: 1.4rem auto; padding: 0 1.2rem; }
  table { border-collapse: collapse; width: 100%; background: #fff; }
  th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid #e7e7e7; vertical-align: top; }
  th { background: #f0f2f7; font-size: .85em; text-transform: uppercase; letter-spacing: .04em; }
  .chip { display: inline-block; background: #e8edf7; border-radius: 9px; padding: 0 .5em; margin: 0 .15em .15em 0; font-size: .82em; white-space: nowrap; }
  .badge-noai { background: #d7f4dd; color: #14532d; font-weight: 600; }
  .badge-nsfw { background: #fde2e2; color: #7f1d1d; font-weight: 600; }
  .badge-suppressed { background: #4b5563; color: #fff; font-weight: 600; }
  .badge-dormant { background: #e5e7eb; color: #374151; font-weight: 600; }
  .badge-open { background: #d7f4dd; color: #14532d; font-weight: 600; }
  .badge-closed { background: #fde2e2; color: #7f1d1d; font-weight: 600; }
  .badge-waitlist { background: #fef3c7; color: #92400e; font-weight: 600; }
  .conf-near_proof { color: #14532d; } .conf-strong { color: #92400e; } .conf-weak { color: #7f1d1d; }
  .stats { display: flex; gap: .8rem; flex-wrap: wrap; margin-bottom: 1.2rem; }
  .stat { background: #fff; border: 1px solid #e7e7e7; border-radius: 8px; padding: .6rem 1rem; min-width: 8.5rem; }
  .stat b { display: block; font-size: 1.5em; }
  .card { background: #fff; border: 1px solid #e7e7e7; border-radius: 8px; padding: 1rem 1.2rem; margin-bottom: 1rem; }
  /* overflow-wrap: an unbroken run (long URL, kaomoji wall) must wrap instead
     of stretching its cell — one wide bio used to widen every table on the page. */
  .bio { background: #f6f6f2; border-left: 3px solid #cbd5e1; padding: .4rem .7rem; white-space: pre-wrap; font-size: .92em; overflow-wrap: anywhere; }
  /* Unbroken-run wrapping is scoped: bios and evidence/details cells carry
     arbitrary text (URLs, kaomoji walls) and must wrap anywhere; everywhere
     else it would break handles and emails mid-word. */
  .wrapany { overflow-wrap: anywhere; }
  td button { overflow-wrap: normal; white-space: nowrap; }
  /* Column-squish guards: dates, platform names and confidences never
     deserve a second line; long names/emails ellipsize instead of folding
     into 2-3 line cells (full value in the title tooltip). */
  td.nowrap { white-space: nowrap; }
  td.trunc { max-width: 13rem; overflow: hidden; text-overflow: ellipsis;
             white-space: nowrap; }
  form.inline { display: inline; }
  button { border: 0; border-radius: 6px; padding: .35rem .8rem; cursor: pointer; font-weight: 600; }
  button.ok { background: #d7f4dd; } button.no { background: #fde2e2; } button.warn { background: #fca311; }
  input[type=text], select { padding: .35rem .5rem; border: 1px solid #cbd5e1; border-radius: 6px; }
  .muted { color: #6b7280; font-size: .9em; }
  h1 { font-size: 1.35rem; } h2 { font-size: 1.1rem; margin-top: 1.6rem; }
  a.linkish, button.linkish { background: none; border: 0; color: #2563eb; cursor: pointer;
    font: inherit; padding: 0; text-decoration: underline; font-weight: 500; }
  /* Sortable column headers */
  th a.sort { color: inherit; text-decoration: none; display: inline-flex; align-items: center; gap: .2em; }
  th a.sort:hover { color: #14213d; }
  th a.sort .arrow { color: #fca311; font-size: .9em; }
  th a.sort .arrow.off { color: #c3c9d4; }
  /* Filter panel */
  details.filters { background: #fff; border: 1px solid #e7e7e7; border-radius: 8px; margin-bottom: 1rem; }
  details.filters > summary { cursor: pointer; padding: .6rem 1rem; font-weight: 600; list-style: none; }
  details.filters > summary::-webkit-details-marker { display: none; }
  details.filters > summary::before { content: "▸ "; color: #fca311; }
  details.filters[open] > summary::before { content: "▾ "; }
  .filter-body { padding: .4rem 1rem 1rem; border-top: 1px solid #eef0f4; }
  .facet { margin-top: .8rem; }
  .facet > .facet-label { font-size: .78em; text-transform: uppercase; letter-spacing: .04em; color: #6b7280; font-weight: 700; margin-bottom: .35rem; }
  .facet-opts { display: flex; flex-wrap: wrap; gap: .2rem .9rem; }
  .facet-opts label { font-size: .9em; white-space: nowrap; display: inline-flex; align-items: center; gap: .25em; cursor: pointer; }
  .platform-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(8.5rem, 1fr)); gap: .1rem .6rem; }
  .pager { display: flex; gap: .5rem; align-items: center; margin: 1rem 0; }
  .pager a, .pager span.cur { padding: .25rem .6rem; border: 1px solid #cbd5e1; border-radius: 6px; text-decoration: none; color: #14213d; }
  .pager span.cur { background: #14213d; color: #fff; border-color: #14213d; }
  .pager a:hover { background: #f0f2f7; }
  /* Bio collapse */
  .bio.clip { max-height: 9.2em; overflow: hidden; position: relative; }
  .bio-toggle { margin-top: .2rem; font-size: .85em; }
  /* Platform stat chips */
  .statline { margin-top: .3rem; display: flex; flex-wrap: wrap; gap: .25rem; }
  .stat-chip { display: inline-block; border-radius: 9px; padding: 0 .5em; font-size: .8em; background: #eef1f7; color: #33415c; white-space: nowrap; }
  .stat-chip.good { background: #d7f4dd; color: #14532d; }
  .stat-chip.warn { background: #fef3c7; color: #92400e; }
  .stat-chip.bad { background: #fde2e2; color: #7f1d1d; }
  /* Pipeline flow diagram (sources/rules pages) */
  .flow { display: flex; flex-wrap: wrap; gap: .4rem; align-items: stretch; margin: 1rem 0 1.6rem; }
  .flowbox { flex: 1 1 10rem; background: #fff; border: 2px solid #14213d; border-radius: 10px; padding: .7rem .9rem; min-width: 11rem; position: relative; }
  .flowbox b { display: block; margin-bottom: .25rem; }
  .flowbox .muted { font-size: .85em; }
  .flowarrow { align-self: center; font-size: 1.6em; color: #fca311; font-weight: 700; padding: 0 .1rem; }
  .flowbox .stepnum { position: absolute; top: -.8rem; left: .7rem; background: #fca311; color: #14213d; font-weight: 800; border-radius: 50%; width: 1.6rem; height: 1.6rem; display: flex; align-items: center; justify-content: center; }
  /* Horizontal bars for source volumes */
  .bar-row { display: grid; grid-template-columns: 14rem 1fr 9rem; gap: .8rem; align-items: center; margin: .35rem 0; }
  .bar-track { background: #eef1f7; border-radius: 6px; height: 1.35rem; position: relative; }
  .bar-fill { background: linear-gradient(90deg, #14213d, #3a5da8); height: 100%; border-radius: 6px; min-width: 2px; }
  .bar-fill.follow { background: linear-gradient(90deg, #9aa7c7, #c3cde4); }
  /* Rule diagrams: nodes, arrows, verdicts */
  .diagram { display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; margin: .5rem 0 .2rem; }
  .node { background: #eef1f7; border: 1.5px solid #33415c; border-radius: 8px; padding: .1rem .55rem; font-size: .88em; white-space: nowrap; }
  .node.acct2 { border-style: dashed; }
  .arrow { color: #33415c; font-weight: 700; }
  .verdict { border-radius: 9px; padding: .05rem .6rem; font-weight: 700; font-size: .85em; white-space: nowrap; }
  .verdict.ok { background: #d7f4dd; color: #14532d; }
  .verdict.no { background: #fde2e2; color: #7f1d1d; }
  .verdict.warn { background: #fef3c7; color: #92400e; }
  .rulegrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(21rem, 1fr)); gap: .9rem; }
  .rulegrid .card { margin-bottom: 0; }
  .livecount { float: right; font-size: .8em; color: #6b7280; }
  /* Review queue */
  .pfp { width: 40px; height: 40px; border-radius: 50%; object-fit: cover;
         vertical-align: middle; background: #eef1f7; flex: none; }
  .pfp.sm { width: 28px; height: 28px; }
  .artist-chip { display: inline-flex; align-items: center; gap: .55rem;
                 background: #f6f8fc; border: 1px solid #dfe6f0; border-radius: 10px;
                 padding: .3rem .8rem .3rem .35rem; margin: .15rem .4rem .15rem 0; }
  .artist-chip .who { line-height: 1.25; }
  .edge-dir { display: inline-block; font-weight: 700; border-radius: 9px;
              padding: 0 .5em; font-size: .8em; white-space: nowrap; }
  .edge-dir.mutual { background: #d7f4dd; color: #14532d; }
  .edge-dir.oneway { background: #fef3c7; color: #92400e; }
  .review-bar { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
  .review-bar .jump { margin-left: auto; display: flex; gap: .9rem; flex-wrap: wrap; }
  .review-bar .jump a { text-decoration: none; color: #14213d; font-weight: 600; }
  .review-bar .jump a:hover { text-decoration: underline; }
  .gate-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(26rem, 1fr)); gap: .9rem; }
  .gate-grid .card { margin-bottom: 0; display: flex; flex-direction: column; }
  .gate-grid .card .grow { flex: 1; }
  .agechip { color: #9aa3b2; font-size: .82em; white-space: nowrap; }
  /* Bio columns must win the table width fight — squeezed platform/reason
     columns once crushed bios to one word per line on the Demoted page. */
  td.biocell { min-width: 24rem; }
</style></head><body>
<header>
  <a class="brand" href="{{ url_for('index') }}" data-dirlink>inkpages review</a>
  <a href="{{ url_for('index') }}" data-dirlink>Directory</a>
  <a href="{{ url_for('review') }}">Review queue {% if pending %}<span class="pill">{{ pending }}</span>{% endif %}</a>
  <a href="{{ url_for('demoted') }}">Demoted {% if demoted_count %}<span class="pill">{{ demoted_count }}</span>{% endif %}</a>
  <a href="{{ url_for('removed') }}">Removed</a>
  <a href="{{ url_for('sources') }}">Sources</a>
  <a href="{{ url_for('rules') }}">Rules</a>
  <a href="{{ url_for('xtag_dashboard') }}">X tags</a>
</header>
<main>{% block content %}{% endblock %}</main>
<dialog id="confirm-dialog" style="border:0;border-radius:10px;padding:1.2rem 1.4rem;max-width:26rem;box-shadow:0 12px 40px rgba(0,0,0,.25)">
  <p class="dlg-msg" style="margin:0 0 1rem"></p>
  <div style="display:flex;gap:.6rem;justify-content:flex-end">
    <button type="button" class="dlg-cancel">Cancel</button>
    <button type="button" class="dlg-ok warn">Confirm</button>
  </div>
</dialog>
<script>
// In-page modal confirmation for any form[data-confirm] (or a submit button
// carrying its own data-confirm) — replaces the native confirm() popup.
(function () {
  var dlg = document.getElementById('confirm-dialog');
  dlg.querySelector('.dlg-cancel').addEventListener('click', function () { dlg.close(); });
  document.querySelectorAll('form').forEach(function (f) {
    f.addEventListener('submit', function (ev) {
      var msg = (ev.submitter && ev.submitter.dataset.confirm) || f.dataset.confirm;
      if (!msg || f.dataset.confirmed) return;
      ev.preventDefault();
      var submitter = ev.submitter;
      dlg.querySelector('.dlg-msg').textContent = msg;
      dlg.querySelector('.dlg-ok').onclick = function () {
        dlg.close();
        f.dataset.confirmed = '1';
        if (submitter && submitter.name) {
          var h = document.createElement('input');
          h.type = 'hidden'; h.name = submitter.name; h.value = submitter.value;
          f.appendChild(h);
        }
        f.submit();
      };
      dlg.showModal();
    });
  });
})();
</script>
<script>
// Directory filter persistence: the last-used query string (filters, sort,
// page) and the Filters panel's open state survive navigating away — the
// Directory nav links replay them.
(function () {
  if (location.pathname === '/') {
    localStorage.setItem('dir:qs', location.search);
    var d = document.querySelector('details.filters');
    if (d) {
      var saved = localStorage.getItem('dir:filtersOpen');
      if (saved === '1') d.setAttribute('open', '');
      else if (saved === '0') d.removeAttribute('open');
      d.addEventListener('toggle', function () {
        localStorage.setItem('dir:filtersOpen', d.open ? '1' : '0');
      });
    }
  } else {
    var qs = localStorage.getItem('dir:qs');
    if (qs) document.querySelectorAll('a[data-dirlink]').forEach(function (a) {
      a.href = '/' + qs;
    });
  }
})();
</script>
<script>
// Keep the scroll position across POST -> redirect -> GET round-trips
// (decide/acknowledge/confirm buttons used to dump the user back at the top).
(function () {
  var key = 'scroll:' + location.pathname;
  document.querySelectorAll('form[method="post" i]').forEach(function (f) {
    f.addEventListener('submit', function () {
      sessionStorage.setItem(key, String(window.scrollY));
    });
  });
  var saved = sessionStorage.getItem(key);
  if (saved !== null) {
    sessionStorage.removeItem(key);
    window.scrollTo(0, parseInt(saved, 10) || 0);
  }
})();
</script>
<script>
// Header select-all: a checkbox with data-checkall="<form id>" toggles every
// row checkbox submitting to that form (checkboxes live outside the form
// element, bound via the form= attribute).
document.querySelectorAll('input[data-checkall]').forEach(function (all) {
  all.addEventListener('change', function () {
    document.querySelectorAll('input[type=checkbox][form="' + all.dataset.checkall + '"]')
      .forEach(function (cb) { cb.checked = all.checked; });
  });
});
</script>
<script>
function toggleBio(btn) {
  var box = btn.previousElementSibling;
  var clipped = box.classList.toggle('clip');
  btn.textContent = clipped ? 'show more' : 'show less';
}
document.querySelectorAll('.bio').forEach(function (box) {
  // Only offer a toggle when content actually overflows the clip height.
  if (box.classList.contains('clip') && box.scrollHeight - box.clientHeight < 4) {
    box.classList.remove('clip');
    if (box.nextElementSibling && box.nextElementSibling.classList.contains('bio-toggle'))
      box.nextElementSibling.remove();
  }
});
</script>
</body></html>""",

"_macros.html": """
{% macro bio(text) %}{% if text %}
  <div class="bio clip">{{ text }}</div>
  <button type="button" class="linkish bio-toggle" onclick="toggleBio(this)">show more</button>
{% endif %}{% endmacro %}

{% macro acct_link(platform, handle, url, display_name=None) %}
  {%- set href = acc_url(platform, handle, url) -%}
  {%- set label = acc_label(platform, handle, display_name) -%}
  {%- if href %}<a href="{{ href }}" target="_blank" rel="noopener">{{ label }}</a>{% else %}{{ label }}{% endif -%}
{% endmacro %}

{# Elegant, platform-aware rendering of accounts.platform_stats. #}
{% macro stats(platform, s) %}{% if s %}
  <div class="statline">
  {% if platform == 'skeb' %}
    {% if s.received_works_count is not none %}<span class="stat-chip">{{ "{:,}".format(s.received_works_count) }} works delivered</span>{% endif %}
    {% if s.received_nsfw_works_count %}<span class="stat-chip">{{ "{:,}".format(s.received_nsfw_works_count) }} 18+</span>{% endif %}
    {% if s.complete_rate is not none %}<span class="stat-chip {{ 'good' if s.complete_rate >= 0.9 else 'warn' }}">{{ (s.complete_rate * 100)|round|int }}% completion</span>{% endif %}
    {% if s.acceptable %}<span class="stat-chip good">accepting requests</span>
    {% elif s.busy %}<span class="stat-chip warn">busy</span>
    {% else %}<span class="stat-chip bad">not accepting</span>{% endif %}
    {% if s.nsfw_acceptable %}<span class="stat-chip">18+ OK</span>{% endif %}
  {% elif platform == 'pixiv' %}
    {% if s.region %}<span class="stat-chip">{{ s.region }}</span>{% endif %}
    {% if s.premium %}<span class="stat-chip good">premium</span>{% endif %}
    {% if s.official %}<span class="stat-chip good">official</span>{% endif %}
  {% elif platform == 'artstation' %}
    {% if s.artstation_position %}<span class="stat-chip">trending #{{ s.artstation_position }}{% if s.artstation_dimension and s.artstation_dimension != 'all' %} ({{ s.artstation_dimension }}){% endif %}</span>{% endif %}
    {% if s.pro_member %}<span class="stat-chip good">pro</span>{% endif %}
  {% elif platform == 'patreon' %}
    {% if s.paid_members %}<span class="stat-chip">{{ "{:,}".format(s.paid_members) }} paid members</span>{% endif %}
    {% if s.graphtreon_category %}<span class="stat-chip">{{ s.graphtreon_category }}</span>{% endif %}
    {% if s.graphtreon_metric %}<span class="stat-chip">{{ s.graphtreon_metric | replace("top-patreon-", "top by ") | replace("top-creators-by-", "top by ") | replace("top-growing-patreon", "fastest growing") | replace("-", " ") }}</span>{% endif %}
  {% elif platform == 'vgen' %}
    {# tags/categories stay in the DB but are too wide to show #}
    {% if s.vgen_reviews %}<span class="stat-chip good">{{ "{:,}".format(s.vgen_reviews) }} reviews{% if s.vgen_rating %} · ★{{ s.vgen_rating }}{% endif %}</span>{% endif %}
  {% elif platform == 'twitter' %}
    {# protected=false stays silent — only the lock is information #}
    {% if s.protected %}<span class="stat-chip warn" title="private account — tweets are followers-only as of the last hydration">🔒 private</span>{% endif %}
  {% else %}
    {# scalars only — a raw list/dict chip once stretched the whole page #}
    {% for k, v in s.items() if v is not none and v is not mapping and (v is string or v is not sequence) %}<span class="stat-chip">{{ k }}: {{ v }}</span>{% endfor %}
  {% endif %}
  </div>
{% endif %}{% endmacro %}
""",

"index.html": """{% extends "base.html" %}{% import "_macros.html" as m %}{% block content %}
<div class="stats">
  <div class="stat"><b>{{ stats.artists }}</b>listed artists</div>
  <div class="stat"><b>{{ stats.badged }}</b>no-AI badged</div>
  <div class="stat"><b>{{ stats.nsfw }}</b>18+ flagged</div>
  <div class="stat"><b>{{ stats.accounts }}</b>accounts</div>
  <div class="stat"><b>{{ stats.suppressed }}</b>suppressed</div>
  <div class="stat"><b>{{ pending }}</b>pending reviews</div>
</div>
{% macro sorth(key, label) %}
  {%- set active = (sort == key) -%}
  {%- set nextdir = 'asc' if (active and dir == 'desc') else ('desc' if active else 'desc') -%}
  <th><a class="sort" href="?{{ qs_with(sort=key, dir=nextdir, page=None) }}">{{ label }}
    <span class="arrow {{ '' if active else 'off' }}">{{ '▲' if (active and dir == 'asc') else '▼' }}</span></a></th>
{% endmacro %}
<form method="get" class="filterform">
<input type="hidden" name="sort" value="{{ sort }}"><input type="hidden" name="dir" value="{{ dir }}">
<p><input type="text" name="q" value="{{ q }}" placeholder="search slug / name / handle" autofocus>
<button class="warn">Search</button>
{% set any_filter = sel_platforms or sel_langs or sel_flags or sel_sources or sel_comms or show18 %}
{% if q or any_filter %}<a class="linkish" href="{{ url_for('index') }}" style="margin-left:.6rem">clear all</a>{% endif %}</p>
<details class="filters" {% if any_filter %}open{% endif %}>
  <summary>Filters{% set n = sel_platforms|length + sel_langs|length + sel_flags|length + sel_sources|length + sel_comms|length + (1 if show18 else 0) %}{% if n %} <span class="pill">{{ n }}</span>{% endif %}</summary>
  <div class="filter-body">
    <div class="facet"><div class="facet-label">Flags</div><div class="facet-opts">
      {% for val, lbl in flag_labels %}
      <label><input type="checkbox" name="flag" value="{{ val }}" {% if val in sel_flags %}checked{% endif %}>{{ lbl }}</label>
      {% endfor %}
      <label><input type="checkbox" name="show18" value="1" {% if show18 %}checked{% endif %}>show 18+</label>
    </div></div>
    <div class="facet"><div class="facet-label">Commissions open <span class="muted">(all selected must hold)</span></div><div class="facet-opts">
      {% for val, lbl in comms_labels %}
      <label><input type="checkbox" name="comms" value="{{ val }}" {% if val in sel_comms %}checked{% endif %}>{{ lbl }}</label>
      {% endfor %}
    </div></div>
    <div class="facet"><div class="facet-label">Source <span class="muted">(any of)</span></div><div class="facet-opts">
      {% for s in source_options %}
      <label><input type="checkbox" name="source" value="{{ s }}" {% if s in sel_sources %}checked{% endif %}>{{ s }}</label>
      {% endfor %}
    </div></div>
    <div class="facet"><div class="facet-label">Language</div><div class="facet-opts">
      {% for l in lang_options %}
      <label><input type="checkbox" name="lang" value="{{ l }}" {% if l in sel_langs %}checked{% endif %}>{{ l }}</label>
      {% endfor %}
    </div></div>
    <div class="facet"><div class="facet-label">Accounts on platform</div><div class="facet-opts platform-grid">
      {% for p in platform_options %}
      <label><input type="checkbox" name="platform" value="{{ p }}" {% if p in sel_platforms %}checked{% endif %}>{{ p }}</label>
      {% endfor %}
    </div></div>
    <p style="margin:.9rem 0 0"><button class="warn">Apply filters</button>
    <a class="linkish" href="{{ url_for('index') }}" style="margin-left:.8rem">Reset filters</a></p>
  </div>
</details>
</form>
<p class="muted">{{ "{:,}".format(total) }} match{{ '' if total == 1 else 'es' }}{% if total > per_page %} · page {{ page }} of {{ pages }}{% endif %}.</p>
<table><tr><th></th>{{ sorth('artist','artist') }}{{ sorth('lang','lang') }}{{ sorth('followers','followers') }}<th>accounts</th><th>flags</th>{{ sorth('updated','updated') }}</tr>
{% for a in artists %}<tr>
  <td>{% if a.avatar_url %}<img src="{{ img_src(a.avatar_url) }}" width="36" height="36" style="border-radius:50%;object-fit:cover" loading="lazy">{% endif %}</td>
  {# When the slug is an opaque pixiv id, lead with the human name. #}
  {%- set id_slug = a.public_slug.isdigit() and a.display_name -%}
  <td class="trunc" title="{{ a.display_name }} /{{ a.public_slug }}"><a href="{{ url_for('artist', artist_id=a.artist_id) }}" target="_blank" rel="noopener"><b>{{ a.display_name if id_slug else a.public_slug }}</b></a><br>
      <span class="muted">{{ ('/' ~ a.public_slug) if id_slug else a.display_name }}</span></td>
  <td class="nowrap">{{ a.language }}</td>
  <td class="nowrap">{{ "{:,}".format(a.followers) if a.followers else "—" }}</td>
  <td>{% for s in a.sources or [] %}<span class="chip badge-noai" style="background:#e8edf7;color:#14213d">{{ s }}</span>{% endfor %}
      {% for acc in a.accounts or [] %}<span class="chip">{{ acc.platform }}: {{ acc_label(acc.platform, acc.handle, acc.display_name) }}{% if acc.stats and acc.stats.protected %} <span title="private account — tweets are followers-only as of the last hydration">🔒</span>{% endif %}</span>{% endfor %}</td>
  {%- set plats = (a.accounts or [])|map(attribute='platform')|list -%}
  <td>{% if a.no_ai_attested %}<span class="chip badge-noai">no-AI</span>{% endif %}
      {% if a.nsfw %}<span class="chip badge-nsfw">18+</span>{% endif %}
      {% if 'twitter' not in plats and 'bluesky' not in plats %}<span class="chip badge-suppressed">no X/bsky</span>{% endif %}
      {% if a.dormant %}<span class="chip badge-dormant">dormant</span>{% endif %}
      {% if a.commissions %}
        {% if a.commissions.skeb_open %}<span class="chip badge-open">skeb open</span>{% endif %}
        {% if a.commissions.pixiv_open %}<span class="chip badge-open">pixiv open</span>{% endif %}
        {% if a.commissions.bio_status %}<span class="chip badge-{{ a.commissions.bio_status }}">comms {{ a.commissions.bio_status }}</span>{% endif %}
      {% endif %}</td>
  <td class="muted" style="white-space:nowrap">{{ a.hydrated_at.strftime('%Y-%m-%d') if a.hydrated_at else "—" }}</td>
</tr>{% endfor %}</table>
{% if pages > 1 %}<div class="pager">
  {% if page > 1 %}<a href="?{{ qs_with(page=page-1) }}">‹ prev</a>{% endif %}
  {% for p in page_window %}
    {% if p == page %}<span class="cur">{{ p }}</span>
    {% elif p == 0 %}<span class="muted">…</span>
    {% else %}<a href="?{{ qs_with(page=p) }}">{{ p }}</a>{% endif %}
  {% endfor %}
  {% if page < pages %}<a href="?{{ qs_with(page=page+1) }}">next ›</a>{% endif %}
</div>{% endif %}
{% endblock %}""",

"artist.html": """{% extends "base.html" %}{% import "_macros.html" as m %}{% block content %}
<h1>{% if avatar %}<img src="{{ img_src(avatar) }}" width="44" height="44" style="border-radius:50%;object-fit:cover;vertical-align:middle"> {% endif %}{{ artist.display_name }} <span class="muted">/{{ artist.public_slug }}</span>
  {% if badge %}<span class="chip badge-noai">no-AI</span>{% endif %}
  {% if nsfw %}<span class="chip badge-nsfw">18+</span>{% endif %}
  {% if suppressed %}<span class="chip badge-suppressed">SUPPRESSED</span>{% endif %}
</h1>
<p class="muted">language: {{ artist.language }} · region: {{ artist.region }} ({{ artist.region_source }}) · status: {{ artist.status }} · created {{ artist.created_at.date() }}</p>

<div class="card">
{% if suppressed %}
  <form class="inline" method="post" action="{{ url_for('unsuppress', artist_id=artist.id) }}">{{ csrf() }}
    <button class="ok">Lift suppression</button>
    <span class="muted">currently: {{ suppressed.reason }} — {{ suppressed.note or "" }}</span></form>
{% else %}
  <form class="inline" method="post" action="{{ url_for('suppress', artist_id=artist.id) }}">{{ csrf() }}
    <select name="reason"><option>opt_out</option><option>impersonation</option><option>ai_use_confirmed</option><option>other</option></select>
    <input type="text" name="note" placeholder="note">
    <button class="no">Suppress (remove from directory)</button></form>
{% endif %}
</div>

{% if pending_items %}
<div class="card" style="border-left:4px solid #fca311">
  <b>Pending review for this artist</b>
  {% for item in pending_items %}
  <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin:.5rem 0">
    {% if item.kind == 'other' %}
      {% for k, v in (item.payload.reasons or {}).items() %}<span class="chip edge-dir oneway">{{ k }}: {{ v }}</span>{% endfor %}
      <span class="muted">#{{ item.id }} · anomaly flag — acknowledging/dismissing changes no data</span>
      <form class="inline" method="post" action="{{ url_for('decide', item_id=item.id, decision='approve') }}">{{ csrf() }}
        <input type="hidden" name="next" value="{{ request.path }}"><button class="ok">Acknowledge</button></form>
      <form class="inline" method="post" action="{{ url_for('decide', item_id=item.id, decision='reject') }}">{{ csrf() }}
        <input type="hidden" name="next" value="{{ request.path }}"><button class="no">Dismiss</button></form>
    {% elif item.kind == 'cluster_merge' %}
      <span class="chip badge-waitlist">merge decision</span>
      <span class="muted">#{{ item.id }} · artists {{ item.payload.artist_ids }} — approve merges them</span>
      <form class="inline" method="post" action="{{ url_for('decide', item_id=item.id, decision='approve') }}"
            data-confirm="Merge these artists?">{{ csrf() }}
        <input type="hidden" name="next" value="{{ request.path }}"><button class="ok">Merge</button></form>
      <form class="inline" method="post" action="{{ url_for('decide', item_id=item.id, decision='reject') }}">{{ csrf() }}
        <input type="hidden" name="next" value="{{ request.path }}"><button class="no">Reject</button></form>
    {% elif item.kind == 'singleton_gate' %}
      <span class="chip badge-waitlist">unlikely artist</span>
      <span class="muted">#{{ item.id }} · {{ item.payload.platform }}:{{ item.payload.handle }}</span>
      <form class="inline" method="post" action="{{ url_for('decide', item_id=item.id, decision='approve') }}">{{ csrf() }}
        <input type="hidden" name="next" value="{{ request.path }}"><button class="ok">List as artist</button></form>
      <form class="inline" method="post" action="{{ url_for('decide', item_id=item.id, decision='reject') }}">{{ csrf() }}
        <input type="hidden" name="next" value="{{ request.path }}"><button class="no">Not an artist</button></form>
    {% endif %}
  </div>
  {% endfor %}
</div>
{% endif %}

<h2>Accounts</h2>
<table><tr><th><input type="checkbox" data-checkall="bulkacc" title="select all"></th><th>platform</th><th>handle</th><th>confidence</th><th>nsfw</th><th>followers</th><th>last post</th><th>comms</th><th>contact</th><th>bio (latest snapshot)</th><th></th></tr>
{% for acc in accounts %}<tr>
  <td><input type="checkbox" form="bulkacc" name="ids" value="{{ acc.id }}"></td>
  <td class="nowrap">{{ acc.platform }}</td>
  <td>{{ m.acct_link(acc.platform, acc.handle, acc.profile_url, acc.display_name) }}
      {{ m.stats(acc.platform, acc.platform_stats) }}</td>
  <td class="conf-{{ acc.confidence }} nowrap">{{ acc.confidence }}</td>
  <td class="nowrap">{% if acc.nsfw %}<span class="chip badge-nsfw">18+</span>{% else %}<span class="muted">safe</span>{% endif %}</td>
  <td class="nowrap">{{ "{:,}".format(acc.followers_count) if acc.followers_count else "—" }}</td>
  <td class="nowrap">{{ acc.last_post_at.date() if acc.last_post_at else "—" }}</td>
  <td>{% if acc.commission_status != 'unknown' %}<span class="chip badge-{{ acc.commission_status }}"
        title="{{ acc.commission_detail }}">{{ acc.commission_status }}
        · {{ acc.commission_checked_at.date() if acc.commission_checked_at }}</span>{% else %}—{% endif %}</td>
  <td class="trunc" title="{{ acc.contact_email or '' }}">{{ acc.contact_email or "—" }}</td>
  <td>{{ m.bio(acc.bio) }}</td>
  <td><form class="inline" method="post" action="{{ url_for('detach', artist_id=artist.id, account_id=acc.id) }}"
       data-confirm="Detach {{ acc.handle }} from this artist? It becomes a connection and will never auto-reattach.">{{ csrf() }}
       <button class="no">detach</button></form></td>
</tr>{% endfor %}</table>
<form id="bulkacc" class="inline" method="post" action="{{ url_for('bulk_detach', artist_id=artist.id) }}"
      data-confirm="Detach all selected accounts? They become connections and will never auto-reattach.">{{ csrf() }}
  <button class="no">Detach selected</button></form>

{% if request.args.get('msg') %}<p class="card" style="border-left:3px solid {{ '#2e7d32' if request.args.get('ok') else '#c62828' }}">{{ request.args.get('msg') }}</p>{% endif %}
<form class="inline" method="post" action="{{ url_for('add_account', artist_id=artist.id) }}" style="margin:8px 0">{{ csrf() }}
  <input type="url" name="url" size="52" required
         placeholder="paste a profile URL to add — e.g. https://x.com/handle">
  <button class="ok">Add account</button>
  <span class="muted">attaches as a human decision (clustering will never undo it); wrong paste → detach</span>
</form>

<h2>Connections</h2>
<p class="muted">Ranked most-likely-own-account first: <span class="chip badge-open">≈ name</span>
rows visibly share a name with this artist's accounts; unresolved same-person
claims next; credits/mention/asset noise last. Related links never merge on
their own — confirm a same-person claim to attach/merge.</p>
<table><tr><th><input type="checkbox" data-checkall="bulkconn" title="select all"></th><th>direction</th><th>account</th><th>belongs to</th><th>followers</th><th>claim</th><th>evidence</th><th></th></tr>
{% set lowsig_collapse = low_signal_count > 5 %}
{% for c in connections %}<tr{% if c.tier == 3 and lowsig_collapse %} class="lowsig" hidden{% endif %}>
  <td><input type="checkbox" form="bulkconn" name="ids" value="{{ c.other_id }}"></td>
  <td class="nowrap">{{ c.direction }}</td>
  <td><span class="chip">{{ c.other_platform }}: {{ m.acct_link(c.other_platform, c.other_handle, c.other_profile_url, c.other_display_name) }}</span>
      {% if c.name_match %}<span class="chip badge-open" title="handle/name matches this artist's accounts">≈ name</span>{% endif %}</td>
  <td class="trunc" title="{{ c.other_artist_slug or '' }}">{% if c.other_artist_id %}<a href="{{ url_for('artist', artist_id=c.other_artist_id) }}">{{ c.other_artist_slug }}</a>{% else %}<span class="muted">unattached</span>{% endif %}</td>
  <td class="nowrap">{{ "{:,}".format(c.other_followers) if c.other_followers else "—" }}</td>
  <td>{% if c.claim == 'same_person' %}<span class="chip badge-waitlist">same-person claim — unresolved</span>
      {% else %}{{ c.relation_hint or "related" }}{% endif %}</td>
  <td class="muted wrapany">{{ c.matched_text or c.evidence_url or "" }}</td>
  <td><form class="inline" method="post" action="{{ url_for('confirm_connection', artist_id=artist.id, account_id=c.other_id) }}"
       data-confirm="{% if c.other_artist_id %}Merge artist {{ c.other_artist_slug }} (via {{ c.other_platform }}:{{ c.other_handle }}) into this artist?{% else %}Confirm {{ c.other_platform }}:{{ c.other_handle }} as the same person and attach it to this artist?{% endif %}">{{ csrf() }}
       <button class="ok">{{ 'merge' if c.other_artist_id else 'attach' }}</button></form>
      <form class="inline" method="post" action="{{ url_for('dismiss_connection', artist_id=artist.id, account_id=c.other_id) }}"
       data-confirm="Remove {{ c.other_platform }}:{{ c.other_handle }} from this artist's connections? It has no relation to the artist and will stay gone even if the link is re-extracted.">{{ csrf() }}
       <button class="no">remove</button></form></td>
</tr>{% else %}<tr><td colspan="8" class="muted">none</td></tr>{% endfor %}</table>
{% if lowsig_collapse %}
<button type="button" class="linkish" style="margin:.4rem 0"
        onclick="var on = this.dataset.open === '1';
                 document.querySelectorAll('tr.lowsig').forEach(function(r){ r.hidden = on; });
                 this.dataset.open = on ? '0' : '1';
                 this.textContent = on ? 'show {{ low_signal_count }} low-signal connections (credits, mentions, sites)'
                                       : 'hide low-signal connections';">
  show {{ low_signal_count }} low-signal connections (credits, mentions, sites)</button>
{% endif %}
<form id="bulkconn" class="inline" method="post" action="{{ url_for('bulk_connections', artist_id=artist.id) }}">{{ csrf() }}
  <button class="ok" name="action" value="confirm"
          data-confirm="Confirm all selected connections as the same person? Floating accounts attach; accounts of other artists merge those artists in.">Attach/merge selected</button>
  <button class="no" name="action" value="dismiss"
          data-confirm="Remove all selected connections? They have no relation to the artist and stay gone even if re-extracted.">Remove selected</button></form>

<h2>Signals</h2>
<table><tr><th>type</th><th>signal</th><th>matched</th><th>account</th><th>first seen</th><th>last seen</th></tr>
{% for s in signals %}<tr>
  <td>{% if s.kind == 'attestation' %}<span class="chip badge-noai">no-AI</span>{% else %}<span class="chip badge-nsfw">18+</span>{% endif %}</td>
  <td>{{ s.signal }}</td><td>{{ s.matched_text }}</td><td>{{ s.handle }}</td>
  <td>{{ s.first_seen.date() }}</td><td>{{ s.last_seen.date() }}</td>
</tr>{% else %}<tr><td colspan="6" class="muted">none</td></tr>{% endfor %}</table>

<h2>Events</h2>
<table><tr><th>when</th><th>event</th><th>actor</th><th>details</th></tr>
{% for e in events %}<tr><td>{{ e.created_at.strftime('%Y-%m-%d %H:%M') }}</td>
<td>{{ e.event }}</td><td>{{ e.actor }}</td><td class="muted wrapany">{{ e.details }}</td></tr>{% endfor %}</table>
{% endblock %}""",

"xtag.html": """{% extends "base.html" %}{% import "_macros.html" as m %}{% block content %}
<h1>X tags <span class="muted" style="font-size:.6em">(x-tag extension dashboard)</span></h1>

<div class="stats">
  <div class="stat"><b>{{ stats.tagged }}</b>tagged total</div>
  <div class="stat"><b>{{ stats.queued }}</b>queued (~${{ "%.2f"|format(queue_cents / 100) }})</div>
  <div class="stat"><b>{{ stats.hydrated }}</b>hydrated</div>
  <div class="stat"><b>{{ stats.artists }}</b>artists</div>
  <div class="stat"><b>{{ stats.removed }}</b>removed</div>
  <div class="stat"><b>${{ "%.2f"|format(spent / 100) }}</b>spent of ${{ "%.0f"|format(cap / 100) }}</div>
</div>

<div class="card">
  <b>Pipeline</b> —
  {% if workers %}<span class="chip badge-open">running</span>
    {% for w in workers %}<code style="font-size:.8em">{{ w }}</code>{% if not loop.last %} · {% endif %}{% endfor %}
  {% else %}<span class="chip">idle</span>{% endif %}
  · {{ "{:,}".format(hubs_uncrawled) }} hub pages await crawling
  {% if log_tail %}
  <details {% if workers %}open{% endif %}><summary>log tail</summary>
    <pre style="max-height:260px;overflow:auto;font-size:.75em;background:#f6f7f9;padding:8px">{{ log_tail }}</pre>
  </details>
  {% endif %}
  {% if workers %}<script>setTimeout(function () { location.reload(); }, 20000);</script>{% endif %}
</div>

<details class="card"><summary><b>Hydration history</b> ({{ flushes|length }} recent flush batches)</summary>
<table><tr><th>when</th><th>profiles</th><th>cost</th></tr>
{% for f in flushes %}<tr><td class="nowrap">{{ f.occurred_at.strftime('%Y-%m-%d %H:%M') }}</td>
<td>{{ f.units }}</td><td>${{ "%.2f"|format(f.cents / 100) }}</td></tr>
{% else %}<tr><td colspan="3" class="muted">none yet</td></tr>{% endfor %}</table>
</details>

{% if request.args.get('msg') %}<p class="card" style="border-left:3px solid #2e7d32">{{ request.args.get('msg') }}</p>{% endif %}

<form method="get" class="filterform" style="margin:12px 0">
<input type="text" name="q" value="{{ qtext }}" placeholder="search handle / name">
<select name="state">
  <option value="">all states</option>
  {% for s in ['queued', 'hydrated', 'listed', 'removed'] %}
  <option value="{{ s }}" {% if state == s %}selected{% endif %}>{{ s }}</option>
  {% endfor %}
</select>
<button class="warn">Filter</button>
{% if qtext or state %}<a class="linkish" href="{{ url_for('xtag_dashboard') }}" style="margin-left:.6rem">clear</a>{% endif %}
</form>

<p class="muted">{{ "{:,}".format(total) }} match{{ '' if total == 1 else 'es' }}{% if pages > 1 %} · page {{ page }} of {{ pages }}{% endif %}.
Removing here = the same rules as the extension: queued rows delete, known rows hide, listed artists get suppressed (reversible from Removed).</p>

<table><tr><th><input type="checkbox" data-checkall="rmform" title="select all on page"></th>
<th></th><th>handle</th><th>name</th><th class="nowrap">followers</th><th>state</th></tr>
{% for r in rows %}<tr>
  <td><input type="checkbox" form="rmform" name="handles" value="{{ r.handle }}"></td>
  <td>{% if r.avatar_url %}<img src="{{ img_src(r.avatar_url) }}" width="28" height="28" style="border-radius:50%;object-fit:cover" loading="lazy">{% endif %}</td>
  <td><a href="https://x.com/{{ r.handle }}" target="_blank" rel="noopener">@{{ r.handle }}</a></td>
  <td class="trunc" title="{{ r.display_name or '' }}">{{ r.display_name or "—" }}</td>
  <td class="nowrap">{{ "{:,}".format(r.followers_count) if r.followers_count is not none else "—" }}</td>
  <td class="nowrap">
    {% if r.listed %}<a href="{{ url_for('artist', artist_id=r.artist_id) }}" target="_blank"><span class="chip badge-open">listed /{{ r.public_slug }}</span></a>
    {% elif r.status in ('hidden', 'deleted') %}<span class="chip badge-suppressed">{{ r.status }}</span>
    {% elif r.artist_id %}<a href="{{ url_for('artist', artist_id=r.artist_id) }}" target="_blank"><span class="chip">artist (unlisted)</span></a>
    {% elif r.last_hydrated %}<span class="chip">hydrated</span>
    {% else %}<span class="chip badge-waitlist">queued</span>{% endif %}
  </td>
</tr>{% else %}<tr><td colspan="6" class="muted">nothing tagged yet</td></tr>{% endfor %}</table>

<form id="rmform" method="post" action="{{ url_for('xtag_remove') }}"
      data-confirm="Remove all selected from inkpages? Queued rows delete, known rows hide, listed artists get suppressed (reversible from the Removed page).">{{ csrf() }}
  <input type="hidden" name="back_state" value="{{ state }}">
  <input type="hidden" name="back_q" value="{{ qtext }}">
  <button class="no">Remove selected</button></form>

{% if pages > 1 %}<div class="pager">
  {% if page > 1 %}<a href="?{{ qs_with(page=page-1) }}">‹ prev</a>{% endif %}
  {% for p in page_window %}
    {% if p == page %}<span class="cur">{{ p }}</span>
    {% elif p == 0 %}<span class="muted">…</span>
    {% else %}<a href="?{{ qs_with(page=p) }}">{{ p }}</a>{% endif %}
  {% endfor %}
  {% if page < pages %}<a href="?{{ qs_with(page=page+1) }}">next ›</a>{% endif %}
</div>{% endif %}
{% endblock %}""",

"demoted.html": """{% extends "base.html" %}{% import "_macros.html" as m %}{% block content %}
<h1>Demoted (no artist evidence)</h1>
<p class="muted">Open-harvest accounts that failed the artist-evidence test. Restore
puts an artist back in the directory and permanently exempts them from auto-demotion.</p>
{% if not items %}<p class="muted">Nothing here.</p>{% endif %}
{% if items %}
<div class="card review-bar" style="position:sticky;top:0;z-index:5">
  <button class="ok" form="bulkrestore"
          data-confirm="Restore all selected artists to the directory? They become permanently exempt from auto-demotion.">Restore selected</button>
  <span class="muted">{{ items|length }} demoted</span>
</div>
{% endif %}
<form id="bulkrestore" method="post" action="{{ url_for('bulk_restore') }}">{{ csrf() }}</form>
<table>{% if items %}<tr><th><input type="checkbox" data-checkall="bulkrestore" title="select all"></th><th></th><th>artist</th><th>platforms</th><th>followers</th><th>reason</th><th>demoted</th><th>bio</th><th></th></tr>{% endif %}
{% for a in items %}<tr>
  <td><input type="checkbox" form="bulkrestore" name="ids" value="{{ a.id }}"></td>
  <td>{% if a.avatar_url %}<img class="pfp" src="{{ img_src(a.avatar_url) }}" loading="lazy">{% else %}<span class="pfp" style="display:inline-block"></span>{% endif %}</td>
  <td class="trunc" title="{{ a.display_name or '' }} /{{ a.public_slug }}">
      <a href="{{ url_for('artist', artist_id=a.id) }}" target="_blank"><b>{{ a.display_name or a.public_slug }}</b></a><br>
      <span class="muted">/{{ a.public_slug }}</span></td>
  <td class="muted">{{ a.platforms or "—" }}</td>
  <td class="nowrap">{{ "{:,}".format(a.followers) if a.followers else "—" }}</td>
  <td>{% if a.reason %}<span class="chip badge-suppressed">{{ a.reason }}</span>{% endif %}</td>
  <td class="nowrap muted">{{ a.demoted_at.date() if a.demoted_at else "—" }}</td>
  <td class="biocell">{{ m.bio(a.bio) }}</td>
  <td><form class="inline" method="post" action="{{ url_for('restore', artist_id=a.id) }}">{{ csrf() }}
      <button class="ok">Restore</button></form></td>
</tr>{% endfor %}</table>
{% endblock %}""",

"removed.html": """{% extends "base.html" %}{% import "_macros.html" as m %}{% block content %}
<h1>Removed from the directory</h1>
<p class="muted">Everything invisible to the public surface and why. Nothing here is
deleted — snapshots, edges and memberships are all kept.</p>

<h2>Suppressed artists ({{ suppressed|length }})</h2>
<p class="muted">Explicit removals — opt-outs, impersonation, confirmed AI use. Lift from the artist page; a suppression survives re-discovery forever.</p>
<table>{% if suppressed %}<tr><th></th><th>artist</th><th>reason</th><th>note</th><th>since</th></tr>{% endif %}
{% for s in suppressed %}<tr>
  <td>{% if s.avatar_url %}<img class="pfp sm" src="{{ img_src(s.avatar_url) }}" loading="lazy">{% endif %}</td>
  <td><a href="{{ url_for('artist', artist_id=s.artist_id) }}" target="_blank"><b>{{ s.display_name or s.public_slug }}</b></a> <span class="muted">/{{ s.public_slug }}</span></td>
  <td><span class="chip badge-suppressed">{{ s.reason }}</span></td>
  <td class="muted">{{ s.note or "" }}</td>
  <td class="nowrap">{{ s.created_at.date() }} <span class="agechip">({{ age_of(s.created_at) }})</span></td>
</tr>{% else %}<tr><td class="muted">none</td></tr>{% endfor %}</table>

<h2>Suppressed accounts ({{ suppressed_accounts|length }})</h2>
<p class="muted">Account-scoped: only this account is hidden, the artist stays listed.</p>
<table>{% if suppressed_accounts %}<tr><th></th><th>account</th><th>artist</th><th>reason</th><th>note</th><th>since</th></tr>{% endif %}
{% for s in suppressed_accounts %}<tr>
  <td>{% if s.avatar_url %}<img class="pfp sm" src="{{ img_src(s.avatar_url) }}" loading="lazy">{% endif %}</td>
  <td><span class="chip">{{ s.platform }}: {{ m.acct_link(s.platform, s.handle, s.profile_url, s.display_name) }}</span></td>
  <td>{% if s.artist_id %}<a href="{{ url_for('artist', artist_id=s.artist_id) }}" target="_blank">{{ s.public_slug }}</a>{% else %}<span class="muted">—</span>{% endif %}</td>
  <td><span class="chip badge-suppressed">{{ s.reason }}</span></td>
  <td class="muted">{{ s.note or "" }}</td>
  <td class="nowrap">{{ s.created_at.date() }} <span class="agechip">({{ age_of(s.created_at) }})</span></td>
</tr>{% else %}<tr><td class="muted">none</td></tr>{% endfor %}</table>

<h2>Hidden accounts ({{ hidden|length }}{{ '+' if hidden|length == 500 }})</h2>
<p class="muted">The standing verification cull: Twitter/Bluesky accounts under
{{ cull_min }} followers (plus any manual hides). Unhide restores the account
everywhere on the next pipeline run — but the cull will re-hide it unless the
follower count has crossed {{ cull_min }}.
{% if hidden_website_count %}<br>Plus <b>{{ "{:,}".format(hidden_website_count) }}</b>
hidden website-link artifacts (asset files, ad scripts, blocklisted domains —
auto-hidden by the junk sweep, not shown: they are extraction artifacts, not
restorable accounts).{% endif %}</p>
<table>{% if hidden %}<tr><th></th><th>account</th><th>followers</th><th>artist</th><th>discovered via</th><th></th></tr>{% endif %}
{% for h in hidden %}<tr>
  <td>{% if h.avatar_url %}<img class="pfp sm" src="{{ img_src(h.avatar_url) }}" loading="lazy">{% endif %}</td>
  <td><span class="chip">{{ h.platform }}: {{ m.acct_link(h.platform, h.handle, h.profile_url, h.display_name) }}</span></td>
  <td class="nowrap">{{ "{:,}".format(h.followers_count) if h.followers_count is not none else "—" }}</td>
  <td>{% if h.artist_id %}<a href="{{ url_for('artist', artist_id=h.artist_id) }}" target="_blank">{{ h.public_slug }}</a>{% else %}<span class="muted">—</span>{% endif %}</td>
  <td class="muted">{{ h.discovered_via }}</td>
  <td><form class="inline" method="post" action="{{ url_for('unhide', account_id=h.id) }}"
       data-confirm="Unhide {{ h.platform }}:{{ h.handle }}? It reappears in the directory (the pipeline cull may re-hide it if still under {{ cull_min }} followers).">{{ csrf() }}
       <button class="ok">unhide</button></form></td>
</tr>{% else %}<tr><td class="muted">none</td></tr>{% endfor %}</table>

<h2>Artists with no visible accounts ({{ invisible|length }})</h2>
<p class="muted">Active artists absent from the directory because every member
account is hidden or deleted.</p>
<table>{% if invisible %}<tr><th>artist</th><th>member accounts</th></tr>{% endif %}
{% for a in invisible %}<tr>
  <td><a href="{{ url_for('artist', artist_id=a.id) }}"><b>{{ a.public_slug }}</b></a> <span class="muted">{{ a.display_name }}</span></td>
  <td class="muted">{{ a.accounts_summary }}</td>
</tr>{% else %}<tr><td class="muted">none</td></tr>{% endfor %}</table>
{% endblock %}""",

"sources.html": """{% extends "base.html" %}{% block content %}
<h1>Where the directory comes from</h1>
<p>Every artist here published their own links — we only collect and connect what
they said about themselves. It happens in four steps:</p>
<div class="flow">
  <div class="flowbox"><span class="stepnum">1</span><b>Discover</b>
    <span class="muted">Public rankings, art feeds and popular-tag searches surface
    artists. Being on a curated list is itself evidence they're an artist.</span></div>
  <span class="flowarrow">→</span>
  <div class="flowbox"><span class="stepnum">2</span><b>Enrich</b>
    <span class="muted">We fetch each profile (bio, followers, links), resolve
    shorteners, and crawl link hubs (Linktree, Carrd, potofu…) they point to.</span></div>
  <span class="flowarrow">→</span>
  <div class="flowbox"><span class="stepnum">3</span><b>Cluster</b>
    <span class="muted">Accounts that point at each other become one artist.
    Every join keeps its evidence — which page said it, when.</span></div>
  <span class="flowarrow">→</span>
  <div class="flowbox"><span class="stepnum">4</span><b>Publish</b>
    <span class="muted">The artist appears with their accounts and — only if they
    said it themselves — a "no AI" badge. Opting out is permanent.</span></div>
</div>

<h2>Discovery sources</h2>
<p class="muted"><b>Primary sources</b> put an artist in the directory by
themselves. <b>Follow-on sources</b> are accounts we met while following an
artist's own links — they only appear as part of an artist, never alone.</p>
{% for s in sources %}
<div class="card">
  <div class="bar-row">
    <div><b>{{ s.label }}</b><br><span class="muted">{{ s.source }}</span></div>
    <div class="bar-track"><div class="bar-fill {{ 'follow' if not s.primary }}"
         style="width: {{ s.pct }}%"></div></div>
    <div><b>{{ "{:,}".format(s.artists) }}</b> artists<br>
         <span class="muted">{{ "{:,}".format(s.accounts) }} accounts</span></div>
  </div>
  <p class="muted" style="margin:.4rem 0 0">{{ s.description }}</p>
  {% if s.derivation %}
  <p style="margin:.4rem 0 0;font-size:.9em"><b>How it's derived:</b>
    <span class="muted">{{ s.derivation }}</span></p>
  {% endif %}
  {% if s.breakdown %}
  <div class="statline" style="margin-top:.45rem">
    {% for label, n in s.breakdown.rows %}<span class="stat-chip">{{ label }} · {{ "{:,}".format(n) }}</span>{% endfor %}
    {% if s.breakdown.more[0] %}<span class="stat-chip">+{{ s.breakdown.more[0] }} more · {{ "{:,}".format(s.breakdown.more[1]) }}</span>{% endif %}
  </div>
  {% endif %}
  <div style="margin-top:.4rem">
    <span class="chip">{{ 'primary source' if s.primary else 'follow-on' }}</span>
    <span class="chip">{{ s.cost }}</span>
    {% for r in s.rules %}<span class="chip badge-waitlist">{{ r }}</span>{% endfor %}
  </div>
</div>
{% endfor %}
{% endblock %}""",

"rules.html": """{% extends "base.html" %}{% block content %}
<h1>How accounts become artists — the rules</h1>
<p>Identity here is a graph: every profile is a <b>node</b>, every self-published
link ("my pixiv is …") is an <b>arrow with evidence attached</b>. Rules decide
when arrows are strong enough to say two accounts are the same person.
Solid boxes are accounts we've verified; a dashed box is the account being judged.</p>

<h2>What merges automatically <span class="livecount">{{ c.same_edges }} same-person links live</span></h2>
<div class="rulegrid">
<div class="card"><b>Mutual links</b>
  <div class="diagram"><span class="node">twitter @ame</span><span class="arrow">⇄</span><span class="node acct2">pixiv Ame</span><span class="verdict ok">merge</span></div>
  <p class="muted">Both profiles point at each other. Nobody can fake both
  directions, so this is near-proof — the backbone of every artist here.</p></div>
<div class="card"><b>Cycles across artists</b>
  <div class="diagram"><span class="node">skeb</span><span class="arrow">→</span><span class="node acct2">pixiv</span><span class="arrow">→</span><span class="node">twitter</span><span class="arrow">→</span><span class="node">skeb</span><span class="verdict ok">merge</span></div>
  <p class="muted">The links form a loop through any of the artist's accounts —
  same proof as a mutual pair, just longer.</p></div>
<div class="card"><b>Platform-verified links</b>
  <div class="diagram"><span class="node">skeb (OAuth)</span><span class="arrow">→</span><span class="node acct2">twitter</span><span class="verdict ok">attach</span></div>
  <p class="muted">Skeb verified the Twitter login itself — the platform vouches,
  not a copyable bio line. Trusted even when the target is famous.</p></div>
<div class="card"><b>Explicit alt mentions</b>
  <div class="diagram"><span class="node">bio: "サブ垢▶@x"</span><span class="arrow">→</span><span class="node acct2">@x</span><span class="verdict ok">attach</span></div>
  <p class="muted">The artist explicitly labels another account as their own
  alt/sub-account.</p></div>
<div class="card"><b>Ordinary links to small accounts</b>
  <div class="diagram"><span class="node">artist bio</span><span class="arrow">→</span><span class="node acct2">&lt;10k followers</span><span class="verdict ok">attach</span></div>
  <p class="muted">One-directional bio links attach when the target is small —
  impersonators don't link to nobodies.</p></div>
<div class="card"><b>Shared-hub reciprocity rescue</b>
  <div class="diagram"><span class="node acct2">famous acct</span><span class="arrow">→</span><span class="node">artist's own Carrd + Patreon</span><span class="verdict ok">attach</span></div>
  <p class="muted">A famous target normally stays unattached (see guards) — but if
  it links back to ≥2 of the artist's own personal pages, that's reciprocity by
  another route.</p></div>
</div>

<h2>What gets held back <span class="livecount">{{ c.flipped }} claims currently held as connections</span></h2>
<div class="rulegrid">
<div class="card"><b>Famous targets</b>
  <div class="diagram"><span class="node">small bio</span><span class="arrow">→</span><span class="node acct2">★ 500k followers</span><span class="verdict warn">connection only</span></div>
  <p class="muted">Impersonators link <i>to</i> famous accounts. Without a link
  back, this stays a visible connection — it upgrades itself the moment
  reciprocity appears. {{ c.flip_prominent }} held now.</p></div>
<div class="card"><b>Second account on one platform</b>
  <div class="diagram"><span class="node">has twitter ✓</span><span class="arrow">→</span><span class="node acct2">another twitter</span><span class="verdict warn">connection only</span></div>
  <p class="muted">Alts are real but doubtful by default; a human can confirm in
  one click. Hard cap: max {{ cap }} accounts per platform per artist
  ({{ c.flip_secondary }} held).</p></div>
<div class="card"><b>Community resources</b>
  <div class="diagram"><span class="node">artist A</span><span class="arrow">↘</span><span class="node acct2">discord/event</span><span class="arrow">↙</span><span class="node">artist B</span><span class="verdict no">never attach</span></div>
  <p class="muted">If two different artists link the same target one-directionally,
  it's a shared resource, not anyone's alt.</p></div>
<div class="card"><b>Same name ≠ same person</b>
  <div class="diagram"><span class="node">twitter @ame</span><span class="arrow">≟</span><span class="node acct2">pixiv "ame"</span><span class="verdict no">never merge</span></div>
  <p class="muted">A matching handle alone never merges anything — that's exactly
  what impersonators copy.</p></div>
</div>

<h2>What never enters the graph</h2>
<div class="rulegrid">
<div class="card"><b>Third-party databases (boorus)</b>
  <p class="muted">Fan-maintained artist databases are used as hints for where to
  look, and are structurally excluded from the published directory — there is no
  join path from hints to the publish view (enforced by a schema test).</p></div>
<div class="card"><b>Scraped data</b>
  <p class="muted">Twitter only via its official paid API (${{ "%.2f"|format(c.spent/100) }} of
  ${{ "%.0f"|format(c.cap_cents/100) }} budget used, every call ledgered).
  Instagram, TikTok, Threads, Weibo, Bilibili and Facebook are display-only:
  handles the artist published are shown, their sites are never fetched.</p></div>
<div class="card"><b>Our opinion about AI use</b>
  <p class="muted">The "no AI" badge is only ever the artist's own words, quoted
  with its source. We never classify, and an accepted correction removes the
  badge quietly — accusations are never published.</p></div>
<div class="card"><b>Link artifacts <span class="livecount">{{ "{:,}".format(c.junk_hidden) }} purged</span></b>
  <div class="diagram"><span class="node acct2">adsbygoogle.js · avatar.gif · site.comhttp://…</span><span class="verdict no">never an account</span></div>
  <p class="muted">Page markup is full of URLs nobody published as identity:
  ad scripts, image CDNs, asset files, reserved paths (vgen.co/uploads),
  glued double-links. Extraction rejects them and a standing pipeline sweep
  retracts any that ever slipped in — growing the blocklist auto-cleans
  history.</p></div>
<div class="card"><b>Roster dumps</b>
  <div class="diagram"><span class="node">group about page</span><span class="arrow">→</span><span class="node acct2">30 member links</span><span class="verdict no">dropped wholesale</span></div>
  <p class="muted">A DeviantArt page mentioning more than 5 other deviants is a
  group roster / watch list, not personal shoutouts — none of its same-platform
  mentions become connections.</p></div>
<div class="card"><b>Collective projects <span class="livecount">{{ "{:,}".format(c.project_flagged) }} flagged</span></b>
  <div class="diagram"><span class="node acct2">"A Haikyuu fanzine 📚"</span><span class="verdict no">parsed out</span></div>
  <p class="muted">Zines, big bangs and anthologies publish reciprocal links
  exactly like a person — their self-description gives them away, and they are
  excluded from clustering, connections and paid hydration entirely.</p></div>
</div>

<h2>Humans stay in charge <span class="livecount">{{ c.pending }} decisions waiting</span></h2>
<div class="rulegrid">
<div class="card"><b>What asks for review</b>
  <p class="muted">Conflicts of 3+ artists, anything over the platform cap, giant
  link components, open-harvest accounts with no artist evidence, and link graphs
  shaped like credits pages (anomaly flags).</p></div>
<div class="card"><b>Human decisions are sacred</b>
  <p class="muted">A manual detach never re-attaches automatically — not even
  through a later merge. A rejected merge ("these are different people") blocks
  that pair from ever auto-merging again. A suppression (opt-out) survives
  re-discovery forever ({{ c.suppressed }} active); an account-scoped
  suppression hides just that account, an artist-scoped one hides the whole
  artist. Accounts hidden by a verification cull stay hidden through every
  refresh until an admin lifts them.</p></div>
<div class="card"><b>Self-healing <span class="livecount">{{ "{:,}".format(c.anomalies_healed) }} flags self-resolved</span></b>
  <p class="muted">All merges trace to stored page snapshots. If a re-parse no
  longer finds the link that justified a join, the join is undone automatically —
  and restored if the evidence returns. Review flags heal the same way: every
  cluster run re-checks pending anomaly items against current data, and a flag
  whose cause was fixed (edges retracted, thresholds raised, artist merged away)
  resolves itself instead of waiting for a human.</p></div>
</div>
{% endblock %}""",

"review.html": """{% extends "base.html" %}{% import "_macros.html" as m %}{% block content %}
{% macro decide_buttons(item, ok='Approve', no='Reject') %}
  <label class="muted"><input type="checkbox" name="items" value="{{ item.id }}" form="bulk"> select</label>
  <form class="inline" method="post" action="{{ url_for('decide', item_id=item.id, decision='approve') }}">{{ csrf() }}<button class="ok">{{ ok }}</button></form>
  <form class="inline" method="post" action="{{ url_for('decide', item_id=item.id, decision='reject') }}">{{ csrf() }}<button class="no">{{ no }}</button></form>
{% endmacro %}
{% macro artist_chip(a, artist_id=None) %}
  <span class="artist-chip">
    {% if a and a.avatar_url %}<img class="pfp" src="{{ img_src(a.avatar_url) }}" loading="lazy">{% else %}<span class="pfp" style="display:inline-block"></span>{% endif %}
    <span class="who"><a href="{{ url_for('artist', artist_id=artist_id or a.id) }}" target="_blank"><b>{{ (a.display_name or a.public_slug) if a else '?' }}</b></a>
    {% if a and a.display_name and a.display_name != a.public_slug %}<span class="muted">/{{ a.public_slug }}</span>{% endif %}<br>
    <span class="muted">{{ "{:,}".format(a.followers) + " followers" if a and a.followers is not none else "followers unknown" }}</span></span>
  </span>
{% endmacro %}
<form id="bulk" method="post" action="{{ url_for('bulk_decide') }}">{{ csrf() }}</form>
<div class="card review-bar" style="position:sticky;top:0;z-index:5">
  <button type="button" onclick="document.querySelectorAll('input[name=items]').forEach(c=>c.checked=true)">Select all</button>
  <button type="button" onclick="document.querySelectorAll('input[name=items]').forEach(c=>c.checked=false)">Clear</button>
  <button class="ok" form="bulk" name="decision" value="approve"
          data-confirm="Approve all selected?">Approve selected</button>
  <button class="no" form="bulk" name="decision" value="reject"
          data-confirm="Reject all selected?">Reject selected</button>
  <span class="jump">
    <a href="#merges">Merges <span class="pill">{{ merge_items|length }}</span></a>
    <a href="#anomalies">Anomalies <span class="pill">{{ anomaly_groups|length }}</span></a>
    <a href="#unlikely">Unlikely artists <span class="pill">{{ gate_total }}</span></a>
    {% if attach_total %}<a href="#attaches">Attaches <span class="pill">{{ attach_total }}</span></a>{% endif %}
  </span>
</div>

<section id="merges">
<h1>Merge decisions <span class="muted">({{ merge_items|length }})</span>
  <button type="button" class="linkish" onclick="selectSection('merges', true)">select all</button></h1>
{% if not merge_items %}<p class="muted">No merge decisions pending.</p>{% endif %}
{% for item in merge_items %}
<div class="card">
  <div style="display:flex;align-items:center;gap:.3rem;flex-wrap:wrap">
    {% for a in item.ctx.artists %}{{ artist_chip(a) }}{% if not loop.last %}<span style="font-size:1.3em;color:#9aa3b2">+</span>{% endif %}{% endfor %}
    {% if item.ctx.any_mutual %}<span class="edge-dir mutual">⇄ has two-way links</span>
    {% else %}<span class="edge-dir oneway">→ one-way evidence only</span>{% endif %}
    <span class="agechip" style="margin-left:auto">#{{ item.id }} · {{ age_of(item.created_at) }} ago</span>
  </div>
  <ul class="muted" style="margin:.6rem 0">
  {% for ev in item.ctx.evidence %}
    <li>{% if ev.mutual %}<span class="edge-dir mutual" title="the target links back">⇄</span>{% else %}<span class="edge-dir oneway" title="no link back from the target">→</span>{% endif %}
        {{ ev.src_platform }}:{% if ev.src_url %}<a href="{{ ev.src_url }}" target="_blank" rel="noopener">{{ acc_label(ev.src_platform, ev.src_handle, ev.src_name) }}</a>{% else %}{{ acc_label(ev.src_platform, ev.src_handle, ev.src_name) }}{% endif %}
        → {{ ev.tgt_platform }}:{% if ev.tgt_url %}<a href="{{ ev.tgt_url }}" target="_blank" rel="noopener">{{ acc_label(ev.tgt_platform, ev.tgt_handle, ev.tgt_name) }}</a>{% else %}{{ acc_label(ev.tgt_platform, ev.tgt_handle, ev.tgt_name) }}{% endif %}
        — {{ ev.evidence_type }}{% if ev.claim == 'related' %} (related){% endif %}
        {% if ev.matched_text %} · “{{ ev.matched_text }}”{% endif %}
        {% if ev.evidence_url %} · <a href="{{ ev.evidence_url }}" target="_blank">source</a>{% endif %}</li>
  {% else %}<li>no live edges found (may already be resolved)</li>{% endfor %}
  </ul>
  <p class="muted">Approve = merge into <b>{{ item.ctx.keeper_slug }}</b>.</p>
  {{ decide_buttons(item, ok='Merge') }}
</div>
{% endfor %}
</section>

<section id="anomalies">
<h1>Anomaly flags <span class="muted">({{ anomaly_count }} across {{ anomaly_groups|length }} artists)</span>
  <button type="button" class="linkish" onclick="selectSection('anomalies', true)">select all</button></h1>
{% if not anomaly_groups %}<p class="muted">Nothing looks off.</p>{% endif %}
{% for g in anomaly_groups %}
{%- set ids = g['items']|map(attribute='id')|join(',') -%}
<div class="card">
  {% if g.public_slug %}
  <div style="display:flex;align-items:center;gap:.3rem;flex-wrap:wrap">
    {{ artist_chip(g.artist or {'public_slug': g.public_slug, 'followers': none, 'avatar_url': none}, artist_id=g.artist_id) }}
    {% for item in g['items'] %}{% for k, v in item.payload.reasons.items() %}<span class="chip edge-dir oneway">{{ k }}: {{ v }}</span> {% endfor %}{% endfor %}
    <span class="agechip" style="margin-left:auto">#{{ ids }}</span>
  </div>
  <p class="muted">Inspect the artist page; detach anything wrong there.
  Acknowledge = looks fine as-is; Dismiss = not worth tracking. Neither
  changes any data.</p>
  {% else %}{% for item in g['items'] %}
  <b>#{{ item.id }} · {{ item.payload.type or 'flag' }}</b> ⚠️
  <pre>{{ item.payload }}</pre>
  {% endfor %}{% endif %}
  <label class="muted"><input type="checkbox" name="items" value="{{ ids }}" form="bulk"> select</label>
  <form class="inline" method="post" action="{{ url_for('bulk_decide') }}">{{ csrf() }}
    <input type="hidden" name="items" value="{{ ids }}">
    <button class="ok" name="decision" value="approve">Acknowledge</button></form>
  <form class="inline" method="post" action="{{ url_for('bulk_decide') }}">{{ csrf() }}
    <input type="hidden" name="items" value="{{ ids }}">
    <button class="no" name="decision" value="reject">Dismiss</button></form>
</div>
{% endfor %}
</section>

<section id="unlikely">
<h1>Unlikely artists <span class="muted">({{ gate_total }})</span>
  <button type="button" class="linkish" onclick="selectSection('unlikely', true)">select all</button></h1>
<p class="muted">Open-harvest accounts with no artist evidence (no art-keyword
bio, no outbound platform links). Approve lists the account as an artist
(permanently exempt from auto-demotion); Reject keeps it out.</p>
{% if not gate_items %}<p class="muted">No pending gates.</p>{% endif %}
<div class="gate-grid">
{% for item in gate_items %}
{% set acc = item.ctx.account %}
<div class="card">
  <div style="display:flex;align-items:center;gap:.6rem">
    {% if acc and acc.avatar_url %}<img class="pfp" src="{{ img_src(acc.avatar_url) }}" loading="lazy">{% else %}<span class="pfp" style="display:inline-block"></span>{% endif %}
    <span style="line-height:1.3">
      <b>{{ m.acct_link(item.payload.platform, (acc.handle if acc else item.payload.handle), (acc.profile_url if acc else none), (acc.display_name if acc else none)) }}</b>
      <span class="muted">on {{ item.payload.platform }}</span><br>
      <span class="muted">{{ "{:,}".format((acc.followers_count if acc else none) or item.payload.followers or 0) }} followers
        · via {{ (acc.discovered_via if acc else none) or item.payload.discovered_via }}</span>
    </span>
    <span class="agechip" style="margin-left:auto">#{{ item.id }} · {{ age_of(item.created_at) }} ago</span>
  </div>
  <div class="grow" style="margin:.5rem 0 0">{{ m.bio(item.ctx.bio) }}</div>
  <div style="margin-top:.6rem">{{ decide_buttons(item, ok='List as artist', no='Not an artist') }}</div>
</div>
{% endfor %}
</div>
{% if gate_total > gate_items|length %}<p class="muted">…and {{ gate_total - gate_items|length }} more (decide some to see the rest).</p>{% endif %}
</section>

{% if attach_items %}
<section id="attaches">
<h1>Attach decisions <span class="muted">({{ attach_total }})</span></h1>
{% for item in attach_items %}
<div class="card">
  <b>#{{ item.id }} · {{ item.ctx.reason or item.kind }}</b> <span class="agechip">{{ age_of(item.created_at) }} ago</span>
  {% if item.kind == 'one_directional_attach' %}
    <p style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
    {% if item.ctx.target_avatar %}<img class="pfp sm" src="{{ img_src(item.ctx.target_avatar) }}" loading="lazy">{% endif %}
    <span><a href="{{ url_for('artist', artist_id=item.ctx.artist_id) }}" target="_blank">{{ item.ctx.artist_slug }}</a>
    ({{ item.ctx.source_handle }}) claims
    <b>{{ item.ctx.target_platform }}: {{ m.acct_link(item.ctx.target_platform, item.ctx.target_handle, item.ctx.target_url) }}</b>
    ({% if item.ctx.target_followers is not none %}{{ "{:,}".format(item.ctx.target_followers) }} followers{% else %}followers unknown — not yet hydrated{% endif %})
    via {{ item.ctx.evidence_type }}{% if item.ctx.matched_text %} · “{{ item.ctx.matched_text }}”{% endif %}
    {% if item.ctx.evidence_url %} · <a href="{{ item.ctx.evidence_url }}" target="_blank">source</a>{% endif %}</span></p>
    <p class="muted">Approve = attach to this artist at strong confidence.</p>
  {% else %}<pre>{{ item.payload }}</pre>{% endif %}
  {{ decide_buttons(item, ok='Attach') }}
</div>
{% endfor %}
{% if attach_total > attach_items|length %}<p class="muted">…and {{ attach_total - attach_items|length }} more attach decisions (decide some to see the rest).</p>{% endif %}
</section>
{% endif %}
<script>
function selectSection(id, on) {
  document.querySelectorAll('#' + id + ' input[name=items]')
    .forEach(function (c) { c.checked = on; });
}
</script>
{% endblock %}""",
}

app = Flask(__name__)
app.jinja_loader = DictLoader(TEMPLATES)

# Columns the directory table can be sorted by → whitelisted SQL (never
# interpolate the raw request value into SQL).
SORT_COLUMNS = {
    "artist": "de.public_slug",
    "lang": "de.language",
    "followers": "followers",
    "updated": "de.hydrated_at",
}
# Flag filters → a SQL predicate on a directory_entries row aliased `de`.
FLAG_SQL = {
    "no_ai": "de.no_ai_attested",
    "nsfw": "de.nsfw",
    "dormant": "de.dormant",
    # Missing both "primary key" platforms — shouldn't happen, worth culling.
    "no_pkey": ("not exists (select 1 from artist_accounts aa "
                "join accounts a on a.id = aa.account_id "
                "join platforms p on p.id = a.platform_id "
                "where aa.artist_id = de.artist_id and aa.removed_at is null "
                "and p.slug in ('twitter', 'bluesky'))"),
}
# nsfw stays in FLAG_SQL (legacy URLs still work) but is not offered as a
# checkbox — 18+ visibility is the single "show 18+" toggle instead.
FLAG_LABELS = [("no_ai", "no-AI"), ("dormant", "dormant"),
               ("no_pkey", "no X/bsky")]

# Commission-open facets → EXISTS predicate on a member account. AND-combined.
# skeb/pixiv "open" mean the platform's own authoritative flag (detail prefixed
# `skeb:` / `pixiv:`); "bio" means a self-attestation parsed from bio/name text.
_COMMS_MEMBER = ("exists (select 1 from artist_accounts aa "
                 "join accounts a on a.id = aa.account_id "
                 "join platforms p on p.id = a.platform_id "
                 "where aa.artist_id = de.artist_id and aa.removed_at is null "
                 "and a.commission_status = 'open' and {cond})")
COMMS_SQL = {
    "skeb": _COMMS_MEMBER.format(cond="p.slug = 'skeb' and a.commission_detail like 'skeb:%%'"),
    "pixiv": _COMMS_MEMBER.format(cond="p.slug = 'pixiv' and a.commission_detail like 'pixiv:%%'"),
    "bio": _COMMS_MEMBER.format(
        cond="coalesce(a.commission_detail, '') not like 'skeb:%%' "
             "and coalesce(a.commission_detail, '') not like 'pixiv:%%'"),
}
COMMS_LABELS = [("skeb", "skeb open"), ("pixiv", "pixiv open"), ("bio", "bio-attested")]

# Sources an artist can be discovered through (directory_entries.sources).
SOURCE_OPTIONS = ["skeb", "bluesky", "twitter", "pixiv", "patreon", "artstation",
                  "deviantart", "vgen", "tagged"]

# Avatar CDNs that 403 without a Referer — proxied through /img (see img_proxy).
PROXY_HOSTS = ("i.pximg.net", "s.pximg.net")
PER_PAGE = 50


def account_url(platform, handle, profile_url):
    """Best profile URL for an account: stored URL first, with a DLsite
    circle-id fallback (its rows often have no profile_url)."""
    if profile_url:
        return profile_url
    if platform == "dlsite" and handle and str(handle).upper().startswith("RG"):
        return f"https://www.dlsite.com/maniax/circle/profile/=/maker_id/{handle}"
    return None


# Platforms whose handle is an opaque id (pixiv user id, youtube channel id) —
# show the human display_name instead when we have one.
_LABEL_BY_NAME = {"pixiv", "youtube"}


def account_label(platform, handle, display_name):
    if platform in _LABEL_BY_NAME and display_name:
        return display_name
    return handle


def age_of(dt):
    """Compact relative age for queue triage: 45m / 6h / 3d / 5w."""
    from datetime import datetime, timezone

    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    for unit, div in (("m", 60), ("h", 3600), ("d", 86400)):
        if secs < div * (60 if unit == "m" else 24 if unit == "h" else 7):
            return f"{max(1, int(secs // div))}{unit}"
    return f"{int(secs // 604800)}w"


def img_src(url):
    """Route hotlink-protected avatar CDNs through the local /img proxy so they
    render in the browser; pass everything else through untouched."""
    if url and any(h in url for h in PROXY_HOSTS):
        from urllib.parse import quote
        return "/img?u=" + quote(url, safe="")
    return url


def qs_with(**overrides):
    """Current query string with overrides applied (value None drops the key),
    preserving multi-valued filters. Used for sort headers and pagination."""
    from urllib.parse import urlencode

    args = request.args.to_dict(flat=False)
    for key, val in overrides.items():
        if val is None:
            args.pop(key, None)
        else:
            args[key] = [val]
    return urlencode([(k, v) for k, vals in args.items() for v in vals])


def page_window(page, pages, span=2):
    """Compact pagination: first, last, and ±span around current, 0 = ellipsis."""
    keep = {1, pages} | {p for p in range(page - span, page + span + 1) if 1 <= p <= pages}
    out, prev = [], 0
    for p in sorted(keep):
        if prev and p - prev > 1:
            out.append(0)
        out.append(p)
        prev = p
    return out


app.jinja_env.globals["acc_url"] = account_url
app.jinja_env.globals["qs_with"] = qs_with
app.jinja_env.globals["img_src"] = img_src
app.jinja_env.globals["acc_label"] = account_label
app.jinja_env.globals["age_of"] = age_of

# CSRF: binding to 127.0.0.1 does not stop a malicious page in the same
# browser from POSTing here. Every mutating form carries a per-process token
# ({{ csrf() }}); a POST without it is rejected. Restarting the server
# invalidates open pages — reload and resubmit.
_CSRF_TOKEN = secrets.token_hex(16)
app.jinja_env.globals["csrf"] = lambda: Markup(
    f'<input type="hidden" name="_csrf" value="{_CSRF_TOKEN}">')

# Short-TTL cache for per-request-invariant aggregates (index stats, facet
# option lists). Cleared on any accepted POST so admin actions show instantly.
_CACHE: dict = {}
_CACHE_TTL = 60


def cached(key, fn):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    val = fn()
    _CACHE[key] = (time.time(), val)
    return val


# Shared secret for the x-tag extension API (form CSRF tokens rotate per
# process, which a browser extension's service worker can't track). Sourced
# from .env INKPAGES_TAG_TOKEN; generated + appended on first use so it
# survives restarts. Paste it into the extension popup once.
_TAG_TOKEN: str | None = None


def tag_token() -> str:
    global _TAG_TOKEN
    if _TAG_TOKEN is None:
        tok = db.env_var("INKPAGES_TAG_TOKEN")
        if not tok:
            tok = secrets.token_hex(16)
            with open(db.ROOT / ".env", "a", encoding="utf-8") as fh:
                fh.write(f"\nINKPAGES_TAG_TOKEN={tok}\n")
            print(f"x-tag: generated INKPAGES_TAG_TOKEN (saved to .env)")
        _TAG_TOKEN = tok
    return _TAG_TOKEN


@app.before_request
def _csrf_protect():
    if request.path.startswith("/api/x/"):
        if not secrets.compare_digest(
                request.headers.get("X-Inkpages-Token", ""), tag_token()):
            abort(403)
        if request.method == "POST":
            _CACHE.clear()
        return
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("_csrf", ""), _CSRF_TOKEN):
            abort(403)
        _CACHE.clear()


@app.route("/img")
def img_proxy():
    """Referer-adding image proxy for hotlink-protected avatar CDNs (pixiv).
    Host-whitelisted to prevent SSRF."""
    import httpx
    from flask import Response

    url = request.args.get("u", "")
    host = url.split("://", 1)[-1].split("/", 1)[0]
    if host not in PROXY_HOSTS:
        return ("", 400)
    try:
        # No redirect following: the whitelist checks only the first hop, so a
        # 3xx could otherwise bounce the proxy to an arbitrary (internal) URL.
        r = httpx.get(url, timeout=10, follow_redirects=False,
                      headers={"Referer": "https://www.pixiv.net/",
                               "User-Agent": "Mozilla/5.0"})
        if r.status_code >= 300:
            return ("", 502)
    except httpx.HTTPError:
        return ("", 502)
    return Response(r.content,
                    content_type=r.headers.get("content-type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"})


def q(conn, sql, params=None):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def pending_count(conn) -> int:
    return q(conn, "select count(*) n from review_items where status = 'pending'")[0]["n"]


def demoted_count(conn) -> int:
    return q(conn, "select count(*) n from artists where status = 'needs_review'")[0]["n"]


@app.route("/")
def index():
    query = request.args.get("q", "").strip()
    sort = request.args.get("sort", "followers")
    if sort not in SORT_COLUMNS:
        sort = "followers"
    direction = "asc" if request.args.get("dir", "").lower() == "asc" else "desc"
    sel_platforms = request.args.getlist("platform")
    sel_langs = request.args.getlist("lang")
    sel_flags = [f for f in request.args.getlist("flag") if f in FLAG_SQL]
    sel_sources = [s for s in request.args.getlist("source") if s in SOURCE_OPTIONS]
    sel_comms = [c for c in request.args.getlist("comms") if c in COMMS_SQL]
    show18 = request.args.get("show18") == "1"
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    where = ["""(%(q)s = '' or de.public_slug ilike '%%' || %(q)s || '%%'
                 or de.display_name ilike '%%' || %(q)s || '%%'
                 or exists (select 1 from artist_accounts aa join accounts a on a.id = aa.account_id
                            where aa.artist_id = de.artist_id and a.handle::text ilike '%%' || %(q)s || '%%'))"""]
    params = {"q": query}
    # Platform facet is conjunctive: require an account on EVERY selected platform.
    for i, plat in enumerate(sel_platforms):
        key = f"plat{i}"
        where.append(f"""exists (select 1 from jsonb_array_elements(de.accounts) el
                                  where el->>'platform' = %({key})s)""")
        params[key] = plat
    if sel_langs:
        where.append("de.language = any(%(langs)s)")
        params["langs"] = sel_langs
    if sel_sources:  # Source facet is disjunctive: any selected source counts.
        where.append("de.sources && %(sources)s::text[]")
        params["sources"] = sel_sources
    where += [FLAG_SQL[f] for f in sel_flags]   # each selected flag is required
    where += [COMMS_SQL[c] for c in sel_comms]  # each selected comms is required
    # SFW by default: 18+ artists are hidden unless the "show 18+" toggle is on
    # or the 18+-only flag filter is selected (which would match nothing here).
    if not show18 and "nsfw" not in sel_flags:
        where.append("not de.nsfw")
    where_sql = " and ".join(where)
    base = f"from directory_entries de where {where_sql}"

    with db.connect() as conn:
        total = q(conn, f"select count(*) n {base}", params)[0]["n"]
        pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
        page = min(page, pages)
        params["lim"] = PER_PAGE
        params["off"] = (page - 1) * PER_PAGE
        artists = q(conn, f"""
            select de.*,
                   (select max(a.followers_count)
                    from artist_accounts aa join accounts a on a.id = aa.account_id
                    where aa.artist_id = de.artist_id and aa.removed_at is null) as followers
            {base}
            order by {SORT_COLUMNS[sort]} {direction} nulls last, de.public_slug asc
            limit %(lim)s offset %(off)s""", params)
        # Filter-independent aggregates — cached (60s TTL, cleared on POST)
        # so every sort/filter/page click doesn't rescan the publish view.
        stats = cached("index_stats", lambda: q(conn, """
            select (select count(*) from directory_entries) as artists,
                   (select count(*) from directory_entries where no_ai_attested) as badged,
                   (select count(*) from directory_entries where nsfw) as nsfw,
                   (select count(*) from accounts) as accounts,
                   (select count(distinct artist_id) from suppressions where lifted_at is null) as suppressed""")[0])
        # Platform facet ordered like the accounts themselves: display_rank
        # first (twitter, bluesky, hubs, ...), most common first within a rank.
        platform_options = cached("platform_options", lambda: [r["p"] for r in q(conn, """
            select c.p from (
                select el->>'platform' as p, count(*) as n
                from directory_entries de, jsonb_array_elements(de.accounts) el
                where el->>'platform' is not null group by 1) c
            join platforms pl on pl.slug = c.p
            where c.p <> 'website'  -- every artist has one; useless as a facet
            order by pl.display_rank, c.n desc, c.p""")])
        lang_options = cached("lang_options", lambda: [r["language"] for r in q(conn,
            "select distinct language from directory_entries order by 1")])
        return render_template("index.html", artists=artists, stats=stats, q=query,
                               sort=sort, dir=direction, sel_platforms=sel_platforms,
                               sel_langs=sel_langs, sel_flags=sel_flags, flag_labels=FLAG_LABELS,
                               show18=show18,
                               sel_sources=sel_sources, source_options=SOURCE_OPTIONS,
                               sel_comms=sel_comms, comms_labels=COMMS_LABELS,
                               platform_options=platform_options, lang_options=lang_options,
                               total=total, page=page, pages=pages, per_page=PER_PAGE,
                               page_window=page_window(page, pages),
                               pending=pending_count(conn), demoted_count=demoted_count(conn))


@app.route("/demoted")
def demoted():
    with db.connect() as conn:
        items = q(conn, """
            select ar.id, ar.public_slug, ar.display_name,
                   (select max(a.followers_count) from artist_accounts aa
                    join accounts a on a.id = aa.account_id
                    where aa.artist_id = ar.id and aa.removed_at is null) as followers,
                   (select a.avatar_url from artist_accounts aa
                    join accounts a on a.id = aa.account_id
                    join platforms p on p.id = a.platform_id
                    where aa.artist_id = ar.id and aa.removed_at is null
                      and a.avatar_url is not null
                    order by p.display_rank, a.followers_count desc nulls last
                    limit 1) as avatar_url,
                   (select string_agg(distinct p.slug, ', ') from artist_accounts aa
                    join accounts a on a.id = aa.account_id
                    join platforms p on p.id = a.platform_id
                    where aa.artist_id = ar.id and aa.removed_at is null) as platforms,
                   (select max(e.created_at) from artist_events e
                    where e.artist_id = ar.id and e.event = 'suppressed') as demoted_at,
                   (select e.details ->> 'reason' from artist_events e
                    where e.artist_id = ar.id and e.event = 'suppressed'
                    order by e.created_at desc limit 1) as reason,
                   (select s.bio_text from artist_accounts aa
                    join account_snapshots s on s.account_id = aa.account_id
                    where aa.artist_id = ar.id and aa.removed_at is null
                    order by s.captured_at desc limit 1) as bio
            from artists ar where ar.status = 'needs_review'
            order by followers desc nulls last""")
        return render_template("demoted.html", items=items,
                               pending=pending_count(conn), demoted_count=len(items))


@app.route("/removed")
def removed():
    from . import policy
    with db.connect() as conn:
        suppressed = q(conn, """
            select s.artist_id, s.reason, s.note, s.created_at,
                   ar.public_slug, ar.display_name,
                   (select a.avatar_url from artist_accounts aa
                    join accounts a on a.id = aa.account_id
                    join platforms p on p.id = a.platform_id
                    where aa.artist_id = ar.id and aa.removed_at is null
                      and a.avatar_url is not null
                    order by p.display_rank, a.followers_count desc nulls last
                    limit 1) as avatar_url
            from suppressions s join artists ar on ar.id = s.artist_id
            where s.lifted_at is null and s.artist_id is not null
            order by s.created_at desc""")
        suppressed_accounts = q(conn, """
            select s.reason, s.note, s.created_at, a.handle::text as handle,
                   a.display_name, a.profile_url, a.avatar_url,
                   p.slug as platform, ar.id as artist_id, ar.public_slug
            from suppressions s
            join accounts a on a.id = s.account_id
            join platforms p on p.id = a.platform_id
            left join artist_accounts aa on aa.account_id = a.id and aa.removed_at is null
            left join artists ar on ar.id = aa.artist_id
            where s.lifted_at is null and s.account_id is not null
            order by s.created_at desc""")
        # Junk-sweep artifacts (website accounts hidden by the extraction
        # blocklist / asset guards) would drown the real cull list ~10k rows
        # deep — they are extraction artifacts, not restorable accounts, so
        # they surface only as a count.
        hidden = q(conn, """
            select a.id, a.handle::text as handle, a.display_name, a.profile_url,
                   a.avatar_url, a.followers_count, a.discovered_via,
                   p.slug as platform, ar.id as artist_id, ar.public_slug
            from accounts a
            join platforms p on p.id = a.platform_id
            left join artist_accounts aa on aa.account_id = a.id and aa.removed_at is null
            left join artists ar on ar.id = aa.artist_id
            where a.status = 'hidden' and p.slug <> 'website'
            order by a.followers_count desc nulls last, a.id
            limit 500""")
        hidden_website_count = q(conn, """
            select count(*) n from accounts a
            join platforms p on p.id = a.platform_id
            where a.status = 'hidden' and p.slug = 'website'""")[0]["n"]
        invisible = q(conn, """
            select ar.id, ar.public_slug, ar.display_name,
                   string_agg(p.slug || ':' || a.handle || ' (' || a.status || ')',
                              ', ' order by a.id) as accounts_summary
            from artists ar
            join artist_accounts aa on aa.artist_id = ar.id and aa.removed_at is null
            join accounts a on a.id = aa.account_id
            join platforms p on p.id = a.platform_id
            where ar.status = 'active' and ar.merged_into is null
              and not exists (select 1 from directory_entries de where de.artist_id = ar.id)
              and not exists (select 1 from suppressions s
                              where s.artist_id = ar.id and s.lifted_at is null)
            group by ar.id, ar.public_slug, ar.display_name
            order by ar.id""")
        return render_template("removed.html", suppressed=suppressed,
                               suppressed_accounts=suppressed_accounts,
                               hidden=hidden, invisible=invisible,
                               hidden_website_count=hidden_website_count,
                               cull_min=policy.CULL_MIN_FOLLOWERS,
                               pending=pending_count(conn),
                               demoted_count=demoted_count(conn))


@app.route("/account/<int:account_id>/unhide", methods=["POST"])
def unhide(account_id):
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("update accounts set status = 'active' where id = %s and status = 'hidden'",
                    (account_id,))
        conn.commit()
    return redirect(url_for("removed"))


# Plain-words metadata for /sources. Keyed by accounts.discovered_via;
# primary=False marks follow-on sources (accounts met by following an artist's
# own links — never listed alone).
SOURCE_META = {
    "skeb_ranking": ("Skeb creator rankings", True, "free",
        "Skeb's own ranked list of commission artists. Skeb also tells us each "
        "creator's login-verified Twitter — the strongest identity link we have.",
        ["curated roster", "OAuth Twitter link"]),
    "pixiv_ranking": ("pixiv rankings", True, "free",
        "pixiv's weekly/monthly illustration rankings (SFW and R-18).",
        ["curated roster"]),
    "pixiv_tag_search": ("pixiv tag search", True, "free",
        "Popularity-sorted search on big tags like オリジナル (original art), "
        "with works their author flagged as AI-generated excluded up front.",
        ["curated by popularity", "AI-flagged works excluded"]),
    "bsky_feed": ("Bluesky art feeds", True, "free",
        "Curated art feeds on Bluesky; the account's own profile record can "
        "also carry self-declared 18+ labels.",
        ["curated roster"]),
    "portfolioday": ("#PortfolioDay (Twitter)", True, "paid",
        "Artists posting the #PortfolioDay hashtag. Anyone can post a hashtag, "
        "so these additionally need artist evidence (an art-flavored bio or "
        "their own links) before they're listed alone.",
        ["open harvest", "needs artist evidence"]),
    "patreon_ranking": ("Graphtreon Patreon rankings", True, "free",
        "Graphtreon's public per-category top lists of Patreon creators "
        "(drawing & painting, comics, animation — including Patreon's "
        "self-declared adult categories) by members, earnings and growth. "
        "Each creator's own Patreon page then supplies their registered "
        "social links.",
        ["curated roster"]),
    "bio_link": ("Linked from an artist's profile", False, "free",
        "An account some artist linked in their bio or profile fields. It only "
        "appears as part of that artist once the link is strong enough — never "
        "on its own.",
        ["joins via clustering only"]),
    "link_hub": ("Found inside a link hub", False, "free",
        "Accounts listed on an artist's own Linktree / Carrd / potofu / "
        "lit.link page — treated exactly like bio links.",
        ["joins via clustering only"]),
    "artstation_ranking": ("ArtStation trending", True, "free",
        "Artists charting on ArtStation's community trending feed (2D first). "
        "ArtStation bot-walls its profile pages, so these arrive with name "
        "and portfolio only — their other accounts connect when the artist's "
        "own bios elsewhere link back to ArtStation.",
        ["curated by trending", "no profile fetch"]),
    "deviantart_popular": ("DeviantArt popular", True, "free",
        "Artists charting on DeviantArt's Popular feed (official public RSS "
        "backend). Each artist's own About page then supplies their "
        "registered social links, watcher count and bio — including "
        "DeviantArt's per-work maturity flags.",
        ["curated by popularity", "full profile fetch"]),
    "vgen_marketplace": ("VGen marketplace", True, "free",
        "Commission artists surfaced at the head of VGen's category "
        "listings, ranked by client review count. Profiles supply their "
        "registered social links, the marketplace's own commissions "
        "OPEN/CLOSED switch, service tags and mature-content flags.",
        ["curated by reviews", "authoritative comms status"]),
    "manual": ("Added by hand", False, "free",
        "An account a human attached from the artist page (pasted profile "
        "URL). Human decisions are never undone by the pipeline.",
        ["human decision"]),
    "manual_tag": ("Tagged on X (browser extension)", True, "paid",
        "Artists tagged with one click while browsing X — the x-tag "
        "extension queues them here, and a paid hydration fetches their "
        "profile. A human looked at the work first, so these are exempt "
        "from the low-follower cull.",
        ["human decision", "curated roster", "cull-exempt"]),
    "bio_mention": ("@-mentioned in a bio", False, "free",
        "Someone @-mentioned this account. Mostly friends and clients, so it "
        "only counts when the artist explicitly marks it as their own alt "
        "account; never fetched until then.",
        ["weakest signal", "alt-claims only"]),
    "hydration": ("Direct profile fetch", False, "free/paid",
        "Accounts first seen when refreshing a known profile.",
        []),
}


# Exact derivation recipe per source — how the roster is assembled, knob for
# knob. Rendered on /sources under the plain-words description.
SOURCE_DERIVATION = {
    "skeb_ranking": "Skeb's Algolia creator ranking, genre=art, taken to rank "
        "1000. Each creator page also yields their OAuth-verified twitter_uid.",
    "pixiv_ranking": "pixiv daily/weekly illustration rankings, ~6 pages "
        "(≈300 works) per run; R18 ranking pages when the premium session is "
        "set. Reruns skim the daily rotation.",
    "pixiv_tag_search": "Premium popularity sort (popular_d) over named tags, "
        "5 pages × 60 works per tag per run; works the author flagged as "
        "AI-generated (ai_type=1) are excluded before parsing. Tag totals below.",
    "bsky_feed": "The author roster of named art feed generators, 100 posts "
        "per feed per run. Feed totals below.",
    "portfolioday": "Paid X recent search over the query below, capped at "
        "--max-posts per run; open harvest, so authors additionally need "
        "artist evidence before listing alone.",
    "portfolioday_mention": "Accounts @-mentioned by #PortfolioDay posts "
        "(same runs as above).",
    "patreon_ranking": "Graphtreon top lists: 4 metrics × 6 art categories "
        "(drawing-painting / comics / animation, SFW + adult), ~100 rows "
        "each; best chart position wins, any adult-category sighting sets "
        "the 18+ platform flag. Category totals below.",
    "artstation_ranking": "ArtStation community trending JSON, dimension "
        "2d → 3d → all, 100/page to feed depth (~500/run).",
    "deviantart_popular": "Popular RSS feeds (overall + digitalart / "
        "traditional / fanart + search-term variants), 60 items/page, feeds "
        "paginate ~6 pages; distinct authors in first-appearance order. "
        "Feeds rotate daily — reruns accumulate.",
    "manual_tag": "One-click tags from the x-tag browser extension while "
        "browsing X (profile header, hover card, or checkbox-selected "
        "follower/following rows). Tags queue as handle-only accounts; the "
        "extension popup's Hydrate button runs the paid users/by fetch and "
        "an optional pipeline pass lists them.",
    "vgen_marketplace": "All ~1,044 category/subject/style listing pages "
        "from VGen's sitemap, each contributing its top-20 relevance-ranked "
        "services; distinct artists then rank by best client review count "
        "and the top N are minted (this cohort: reviews 17 min / 32 median "
        "/ 657 max). Listing totals below count every listing an artist "
        "surfaced on.",
}

# Live provenance breakdowns (label, artists) straight from
# discovery_details / platform_stats — which tag, feed, category or query
# actually produced the accounts.
SOURCE_BREAKDOWN_SQL = {
    "pixiv_tag_search": """
        select (discovery_details ->> 'tag')
               || case when (discovery_details ->> 'r18')::bool
                       then ' · R18' else '' end as label, count(*) as count
        from accounts where discovered_via = 'pixiv_tag_search'
        group by 1 order by 2 desc""",
    "pixiv_ranking": """
        select coalesce(discovery_details ->> 'mode', 'ranking')
               || case when coalesce((discovery_details ->> 'r18')::bool, false)
                       then ' · R18' else '' end as label, count(*) as count
        from accounts where discovered_via = 'pixiv_ranking'
        group by 1 order by 2 desc""",
    "bsky_feed": """
        select coalesce(discovery_details ->> 'name', '(unnamed feed)') as label, count(*) as count
        from accounts where discovered_via = 'bsky_feed'
        group by 1 order by 2 desc""",
    "portfolioday": """
        select coalesce(discovery_details ->> 'query', '(query not recorded)') as label, count(*) as count
        from accounts where discovered_via = 'portfolioday'
        group by 1 order by 2 desc""",
    "patreon_ranking": """
        select coalesce(discovery_details ->> 'category', '?') as label, count(*) as count
        from accounts where discovered_via = 'patreon_ranking'
        group by 1 order by 2 desc""",
    "artstation_ranking": """
        select 'trending · ' || coalesce(discovery_details ->> 'dimension', '?') as label, count(*) as count
        from accounts where discovered_via = 'artstation_ranking'
        group by 1 order by 2 desc""",
    "deviantart_popular": """
        select coalesce(discovery_details ->> 'feed', 'popular (feed not recorded)') as label, count(*) as count
        from accounts where discovered_via = 'deviantart_popular'
        group by 1 order by 2 desc""",
    "vgen_marketplace": """
        select cat as label, count(*) as count
        from accounts, lateral jsonb_array_elements_text(
             coalesce(platform_stats -> 'vgen_categories', '[]'::jsonb)) cat
        where discovered_via = 'vgen_marketplace'
        group by 1 order by 2 desc""",
}

_BREAKDOWN_SHOWN = 14


@app.route("/sources")
def sources():
    with db.connect() as conn:
        rows = q(conn, """
            select a.discovered_via as source, count(distinct a.id) as accounts,
                   count(distinct aa.artist_id) as artists
            from accounts a
            left join artist_accounts aa on aa.account_id = a.id and aa.removed_at is null
            group by 1""")
        max_artists = max((r["artists"] for r in rows), default=1) or 1
        breakdowns: dict[str, list] = {}
        for src, sql in SOURCE_BREAKDOWN_SQL.items():
            b = q(conn, sql)
            if b:
                shown = b[:_BREAKDOWN_SHOWN]
                rest = b[_BREAKDOWN_SHOWN:]
                breakdowns[src] = {
                    "rows": [(r["label"], r["count"]) for r in shown],
                    "more": (len(rest), sum(r["count"] for r in rest)),
                }
        entries = []
        for r in rows:
            label, primary, cost, description, rules = SOURCE_META.get(
                r["source"], (r["source"], False, "free", "", []))
            entries.append({**r, "label": label, "primary": primary, "cost": cost,
                            "description": description, "rules": rules,
                            "derivation": SOURCE_DERIVATION.get(r["source"]),
                            "breakdown": breakdowns.get(r["source"]),
                            "pct": max(1, round(100 * r["artists"] / max_artists))})
        entries.sort(key=lambda e: (not e["primary"], -e["artists"]))
        return render_template("sources.html", sources=entries,
                               pending=pending_count(conn), demoted_count=demoted_count(conn))


@app.route("/rules")
def rules():
    from . import policy
    from .twitter import spend_cap_cents

    with db.connect() as conn:
        c = q(conn, """
            select
              (select count(*) from identity_edges
               where status = 'present' and claim = 'same_person') as same_edges,
              (select count(*) from identity_edges where status = 'present'
               and claim = 'related' and relation_hint in
                   ('unreciprocated_prominent', 'secondary_link', 'over_platform_cap')) as flipped,
              (select count(*) from identity_edges where status = 'present'
               and claim = 'related' and relation_hint = 'unreciprocated_prominent') as flip_prominent,
              (select count(*) from identity_edges where status = 'present'
               and claim = 'related' and relation_hint = 'secondary_link') as flip_secondary,
              (select count(*) from review_items where status = 'pending') as pending,
              (select count(distinct artist_id) from suppressions
               where lifted_at is null) as suppressed,
              (select count(*) from accounts a
               join platforms p on p.id = a.platform_id
               where a.status = 'hidden' and p.slug = 'website') as junk_hidden,
              (select count(*) from review_items
               where decided_by in ('pipeline:anomaly_cleared',
                                    'pipeline:merged_away',
                                    'pipeline:threshold_raise')) as anomalies_healed,
              (select count(*) from accounts where project) as project_flagged,
              (select coalesce(sum(est_cost_cents), 0) from api_usage
               where service = 'x_api') as spent""")[0]
        c["cap_cents"] = spend_cap_cents()
        return render_template("rules.html", c=c, cap=policy.MAX_SAME_PLATFORM,
                               pending=c["pending"], demoted_count=demoted_count(conn))


def _restore_one(cur, artist_id: int) -> None:
    cur.execute("update artists set status = 'active', updated_at = now() where id = %s",
                (artist_id,))
    cur.execute("""insert into artist_events (artist_id, event, actor, details)
                   values (%s, 'unsuppressed', 'admin:review-ui',
                           '{"reason": "restored_from_demotion"}')""", (artist_id,))


@app.route("/artist/<int:artist_id>/restore", methods=["POST"])
def restore(artist_id):
    with db.connect() as conn, conn.cursor() as cur:
        _restore_one(cur, artist_id)
        conn.commit()
    return redirect(url_for("demoted"))


@app.route("/demoted/bulk_restore", methods=["POST"])
def bulk_restore():
    with db.connect() as conn, conn.cursor() as cur:
        for artist_id in request.form.getlist("ids"):
            _restore_one(cur, int(artist_id))
        conn.commit()
    return redirect(url_for("demoted"))


def _norm_name(s: str | None) -> str:
    """Normalize a handle/name for similarity: lowercase alphanumerics only,
    platform suffixes stripped (ap1os.bsky.social ~ ap1os, fy_fp7 ~ fyfp7)."""
    if not s:
        return ""
    s = re.sub(r"\.(?:bsky\.social|carrd\.co|tumblr\.com|fanbox\.cc|booth\.pm)$",
               "", s.lower())
    return re.sub(r"[^a-z0-9]", "", s)


def _name_similarity(candidates: list[str], member_names: set[str]) -> float:
    """Best similarity between any candidate name and any member name.
    1.0 exact, 0.9 containment (≥4 chars), else difflib ratio."""
    from difflib import SequenceMatcher

    best = 0.0
    for cand in {_norm_name(c) for c in candidates if c}:
        if not cand:
            continue
        for member in member_names:
            if not member:
                continue
            if cand == member:
                return 1.0
            if len(cand) >= 4 and len(member) >= 4 and (
                    cand in member or member in cand):
                best = max(best, 0.9)
            else:
                best = max(best, SequenceMatcher(None, cand, member).ratio())
    return best


# Connection ordering: most-likely-own-account first. Score ≥ this = the
# names visibly match ("mikan_art" linking "mikanart.tumblr.com"), which is
# how a human instantly separates the artist's own accounts from credits.
NAME_MATCH_MIN = 0.75

# Hints that are almost always credits/features/infra rather than the
# artist's own accounts — they sink to the bottom unless the name matches.
_LOW_SIGNAL_HINTS = {"same_platform_mention", "hub_credits", "website"}


@app.route("/artist/<int:artist_id>")
def artist(artist_id):
    with db.connect() as conn:
        artist = q(conn, "select * from artists where id = %s", (artist_id,))[0]
        # A merged-away artist is a husk (memberships moved to the keeper) —
        # its page is an empty, confusing shell. Follow the pointer (chains
        # are collapsed to one hop at merge time).
        if artist["merged_into"]:
            return redirect(url_for("artist", artist_id=artist["merged_into"]))
        accounts = q(conn, """
            select a.id, a.handle::text, a.display_name, a.profile_url, a.avatar_url, a.followers_count, a.status,
                   a.platform_stats, a.last_post_at, a.contact_email, a.commission_status,
                   a.commission_confidence, a.commission_detail, a.commission_checked_at,
                   aa.confidence, p.slug as platform, p.display_only,
                   exists (select 1 from content_flags cf
                           where cf.account_id = a.id and cf.active
                             and cf.flag = 'nsfw') as nsfw,
                   (select s.bio_text from account_snapshots s
                    where s.account_id = a.id order by s.captured_at desc limit 1) as bio
            from artist_accounts aa
            join accounts a on a.id = aa.account_id
            join platforms p on p.id = a.platform_id
            where aa.artist_id = %s and aa.removed_at is null
            order by p.display_rank, a.followers_count desc nulls last""", (artist_id,))
        connections = q(conn, """
            select distinct on (oa.id)
                   e.id as edge_id, e.claim,
                   case when e.source_account_id = m.account_id then 'outgoing' else 'incoming' end as direction,
                   e.relation_hint, e.matched_text, e.evidence_url,
                   oa.id as other_id, oa.handle::text as other_handle,
                   oa.display_name as other_display_name,
                   oa.profile_url as other_profile_url, oa.followers_count as other_followers,
                   op.slug as other_platform,
                   oar.id as other_artist_id, oar.public_slug as other_artist_slug
            from identity_edges e
            join (select account_id from artist_accounts
                  where artist_id = %(id)s and removed_at is null) m
              on m.account_id in (e.source_account_id, e.target_account_id)
            join accounts oa on oa.id = case when e.source_account_id = m.account_id
                                             then e.target_account_id else e.source_account_id end
            join platforms op on op.id = oa.platform_id
            left join artist_accounts oaa on oaa.account_id = oa.id and oaa.removed_at is null
            left join artists oar on oar.id = oaa.artist_id and oar.merged_into is null
            -- `related` edges are ordinary connections; `same_person` edges to
            -- a NON-member are unresolved claims (target sits in another
            -- artist, or clustering hasn't attached it) — they must be visible
            -- and attachable here, not silently absent.
            where e.status = 'present'
              -- Skip edges whose other end is already an account of this artist:
              -- that link is internal to a merge, not an external connection.
              and oa.id not in (select account_id from artist_accounts
                                where artist_id = %(id)s and removed_at is null)
              -- Zines/collective projects are parsed out entirely — their
              -- participant-roster wires are not connections worth reviewing.
              and not oa.project
            order by oa.id, e.claim desc, e.id""", {"id": artist_id})
        signals = q(conn, """
            select 'attestation' as kind, att.signal, att.matched_text, a.handle::text,
                   att.first_seen, att.last_seen
            from attestations att join accounts a on a.id = att.account_id
            where att.active and att.account_id in
                  (select account_id from artist_accounts where artist_id = %(id)s and removed_at is null)
            union all
            select 'content_flag', cf.signal, cf.matched_text, a.handle::text,
                   cf.first_seen, cf.last_seen
            from content_flags cf join accounts a on a.id = cf.account_id
            where cf.active and cf.account_id in
                  (select account_id from artist_accounts where artist_id = %(id)s and removed_at is null)
            order by first_seen""", {"id": artist_id})
        events = q(conn, "select * from artist_events where artist_id = %s order by created_at",
                   (artist_id,))
        suppressed_rows = q(conn, """select * from suppressions
                                     where artist_id = %s and lifted_at is null limit 1""", (artist_id,))

        # Rank connections the way a human reads them: rows whose handle or
        # name matches the artist's own names first (90% of real own-account
        # links are visually obvious), then unresolved same-person claims,
        # then ordinary related links; credits/mention/website noise last.
        member_names = {_norm_name(a["handle"]) for a in accounts} \
            | {_norm_name(a["display_name"]) for a in accounts}
        member_names.discard("")
        for c in connections:
            c["name_score"] = _name_similarity(
                [c["other_handle"], c["other_display_name"]], member_names)
            c["name_match"] = c["name_score"] >= NAME_MATCH_MIN
            if c["name_match"]:
                tier = 0
            elif c["claim"] == "same_person":
                tier = 1
            elif (c["relation_hint"] or "") not in _LOW_SIGNAL_HINTS:
                tier = 2
            else:
                tier = 3
            c["tier"] = tier
        connections.sort(key=lambda c: (
            c["tier"], -c["name_score"], -(c["other_followers"] or 0)))
        low_signal_count = sum(1 for c in connections if c["tier"] == 3)

        # Pending review items about THIS artist — decidable in place, so
        # inspecting an anomaly/merge never requires bouncing back to the
        # queue. Gates key on member accounts (rare but possible after a
        # later attach).
        pending_items = q(conn, """
            select * from review_items
            where status = 'pending'
              and ((kind = 'other' and (payload->>'artist_id')::bigint = %(id)s)
                or (kind = 'cluster_merge'
                    and (payload->>'artist_ids')::jsonb @> to_jsonb(%(id)s::bigint))
                or (kind = 'singleton_gate'
                    and (payload->>'account_id')::bigint in
                        (select account_id from artist_accounts
                         where artist_id = %(id)s and removed_at is null)))
            order by created_at""", {"id": artist_id})
        # Title matches the directory's name rule (migration 0024): the
        # top-display_rank visible account wins (twitter/bsky over pixiv),
        # regardless of membership confidence.
        artist["display_name"] = next(
            (a["display_name"] or a["handle"] for a in accounts
             if a["status"] in ("active", "unknown") and (a["display_name"] or a["handle"])),
            artist["display_name"])
        return render_template(
            "artist.html", artist=artist, accounts=accounts, signals=signals,
            connections=connections, low_signal_count=low_signal_count,
            pending_items=pending_items, events=events,
            avatar=next((a["avatar_url"] for a in accounts
                         if a.get("avatar_url")), None),
            suppressed=suppressed_rows[0] if suppressed_rows else None,
            badge=any(s["kind"] == "attestation" for s in signals),
            nsfw=any(s["kind"] == "content_flag" for s in signals),
            pending=pending_count(conn), demoted_count=demoted_count(conn))


def _detach_one(conn, artist_id, account_id):
    with conn.cursor() as cur:
        cur.execute(
            """update artist_accounts set removed_at = now()
               where artist_id = %s and account_id = %s and removed_at is null""",
            (artist_id, account_id))
        cur.execute(
            """insert into artist_events (artist_id, event, actor, details)
               values (%s, 'account_removed', 'admin:review-ui', %s)""",
            (artist_id, json.dumps({"account_id": account_id,
                                    "reason": "manual_detach"})))
        cur.execute(
            """update identity_edges e set claim = 'related', relation_hint = 'manual_detach'
               where e.claim = 'same_person' and e.status = 'present'
                 and ((e.target_account_id = %(acc)s and e.source_account_id in
                       (select account_id from artist_accounts
                        where artist_id = %(ar)s and removed_at is null))
                   or (e.source_account_id = %(acc)s and e.target_account_id in
                       (select account_id from artist_accounts
                        where artist_id = %(ar)s and removed_at is null)))""",
            {"acc": account_id, "ar": artist_id})


@app.route("/artist/<int:artist_id>/detach/<int:account_id>", methods=["POST"])
def detach(artist_id, account_id):
    """Remove an account from an artist: membership closes (admin event, so
    clustering never re-attaches it) and connecting edges become related, so
    the account stays visible as a connection."""
    with db.connect() as conn:
        _detach_one(conn, artist_id, account_id)
        conn.commit()
    return redirect(url_for("artist", artist_id=artist_id))


def _confirm_one(conn, artist_id, account_id):
    with conn.cursor() as cur:
        cur.execute("""select artist_id from artist_accounts
                       where account_id = %s and removed_at is null""", (account_id,))
        row = cur.fetchone()
        other_artist = row[0] if row else None
        if other_artist and other_artist != artist_id:
            from .cluster import merge_artists
            merge_artists(conn, artist_id, [other_artist], actor="admin:review-ui")
            # A pending merge question for this pair is now answered.
            cur.execute(
                """update review_items
                   set status = 'approved', resolved_at = now(),
                       decided_by = 'admin:review-ui'
                   where kind = 'cluster_merge' and status = 'pending'
                     and payload ->> 'artist_ids' = %s""",
                (json.dumps(sorted([artist_id, other_artist])),))
        elif other_artist is None:
            cur.execute("""insert into artist_accounts (artist_id, account_id, confidence, added_by)
                           values (%s, %s, 'strong', 'human')""", (artist_id, account_id))
            cur.execute("""insert into artist_events (artist_id, event, actor, details)
                           values (%s, 'account_added', 'admin:review-ui', %s)""",
                        (artist_id, json.dumps({"account_id": account_id,
                                                "reason": "manual_confirm_connection"})))
        cur.execute(
            """update identity_edges e set claim = 'same_person', relation_hint = 'manual_confirm'
               where e.claim = 'related' and e.status = 'present'
                 and ((e.source_account_id = %(acc)s and e.target_account_id in
                       (select account_id from artist_accounts
                        where artist_id = %(ar)s and removed_at is null))
                   or (e.target_account_id = %(acc)s and e.source_account_id in
                       (select account_id from artist_accounts
                        where artist_id = %(ar)s and removed_at is null)))""",
            {"acc": account_id, "ar": artist_id})


@app.route("/artist/<int:artist_id>/confirm/<int:account_id>", methods=["POST"])
def confirm_connection(artist_id, account_id):
    """Inverse of detach: a human vouches that a 'related' connection is in fact
    the same person. If the account belongs to another artist, merge them;
    otherwise attach the floating account. Either way the connecting edges
    become same_person so re-extraction keeps them merged."""
    with db.connect() as conn:
        _confirm_one(conn, artist_id, account_id)
        conn.commit()
    return redirect(url_for("artist", artist_id=artist_id))


def _dismiss_one(conn, artist_id, account_id):
    with conn.cursor() as cur:
        cur.execute(
            """update identity_edges e
               set status = 'dismissed', relation_hint = 'manual_dismiss'
               where e.status = 'present'
                 and ((e.target_account_id = %(acc)s and e.source_account_id in
                       (select account_id from artist_accounts
                        where artist_id = %(ar)s and removed_at is null))
                   or (e.source_account_id = %(acc)s and e.target_account_id in
                       (select account_id from artist_accounts
                        where artist_id = %(ar)s and removed_at is null)))""",
            {"acc": account_id, "ar": artist_id})
        # Logged as an admin account_removed so the clustering guard also
        # refuses to ever auto-attach this account to this artist.
        cur.execute(
            """insert into artist_events (artist_id, event, actor, details)
               values (%s, 'account_removed', 'admin:review-ui', %s)""",
            (artist_id, json.dumps({"account_id": account_id,
                                    "reason": "connection_dismissed"})))


@app.route("/artist/<int:artist_id>/dismiss/<int:account_id>", methods=["POST"])
def dismiss_connection(artist_id, account_id):
    """A human rules a connection is pure noise — no relation to the artist.
    Every edge between the artist's members and that account is dismissed:
    hidden everywhere and never resurrected by re-extraction (upsert_edge
    skips rows whose status is 'dismissed')."""
    with db.connect() as conn:
        _dismiss_one(conn, artist_id, account_id)
        conn.commit()
    return redirect(url_for("artist", artist_id=artist_id))


@app.route("/artist/<int:artist_id>/bulk_detach", methods=["POST"])
def bulk_detach(artist_id):
    with db.connect() as conn:
        for account_id in request.form.getlist("ids"):
            _detach_one(conn, artist_id, int(account_id))
        conn.commit()
    return redirect(url_for("artist", artist_id=artist_id))


@app.route("/artist/<int:artist_id>/bulk_connections", methods=["POST"])
def bulk_connections(artist_id):
    """Bulk confirm (attach/merge) or remove for selected connection rows —
    same per-account logic as the row buttons, one decision dialog."""
    action = request.form.get("action")
    one = _confirm_one if action == "confirm" else _dismiss_one
    with db.connect() as conn:
        for account_id in request.form.getlist("ids"):
            one(conn, artist_id, int(account_id))
        conn.commit()
    return redirect(url_for("artist", artist_id=artist_id))


@app.route("/artist/<int:artist_id>/add_account", methods=["POST"])
def add_account(artist_id):
    """Manually attach an account from a pasted profile URL. The URL is parsed
    with the same extraction patterns as bios, so platform + handle are always
    canonical; unknown domains become a personal-website account. A human add
    is sacred: added_by='human', and the pipeline never closes those."""
    from .extract import find_platform_links, find_website_links

    url = (request.form.get("url") or "").strip()
    links = find_platform_links(url) or find_website_links(url)

    def back(msg, ok=False):
        return redirect(url_for("artist", artist_id=artist_id, msg=msg,
                                ok=1 if ok else None))

    if not links:
        return back("could not parse that URL — no platform pattern matched")
    link = links[0]
    with db.connect() as conn, conn.cursor() as cur:
        platform_id = db.platform_ids(conn).get(link.platform)
        if platform_id is None:
            return back(f"platform {link.platform} is not seeded")
        account_id = db.get_or_create_account(
            conn, platform_id, native_id=link.native_id,
            handle=link.handle or link.native_id, profile_url=link.url,
            discovered_via="manual",
            discovery_details={"artist_id": artist_id, "via": "review-ui"},
        )
        cur.execute("""select aa.artist_id, ar.public_slug from artist_accounts aa
                       join artists ar on ar.id = aa.artist_id
                       where aa.account_id = %s and aa.removed_at is null""",
                    (account_id,))
        row = cur.fetchone()
        if row and row[0] == artist_id:
            conn.commit()
            return back(f"{link.platform}:{link.handle or link.native_id} "
                        "is already a member of this artist", ok=True)
        if row:
            conn.commit()
            return back(f"{link.platform}:{link.handle or link.native_id} already "
                        f"belongs to /{row[1]} — merge via its Connections row "
                        "instead of adding it here")
        cur.execute("""insert into artist_accounts (artist_id, account_id, confidence, added_by)
                       values (%s, %s, 'strong', 'human')""", (artist_id, account_id))
        cur.execute("""insert into artist_events (artist_id, event, actor, details)
                       values (%s, 'account_added', 'admin:review-ui', %s)""",
                    (artist_id, json.dumps({"account_id": account_id,
                                            "reason": "manual_add", "url": url})))
        conn.commit()
    return back(f"added {link.platform}:{link.handle or link.native_id}", ok=True)


@app.route("/artist/<int:artist_id>/suppress", methods=["POST"])
def suppress(artist_id):
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("""insert into suppressions (artist_id, reason, note, requested_by)
                       values (%s, %s, %s, 'admin:review-ui')""",
                    (artist_id, request.form["reason"], request.form.get("note") or None))
        cur.execute("""insert into artist_events (artist_id, event, actor, details)
                       values (%s, 'suppressed', 'admin:review-ui', %s)""",
                    (artist_id, json.dumps({"reason": request.form["reason"]})))
        conn.commit()
    return redirect(url_for("artist", artist_id=artist_id))


@app.route("/artist/<int:artist_id>/unsuppress", methods=["POST"])
def unsuppress(artist_id):
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("update suppressions set lifted_at = now() where artist_id = %s and lifted_at is null",
                    (artist_id,))
        cur.execute("""insert into artist_events (artist_id, event, actor)
                       values (%s, 'unsuppressed', 'admin:review-ui')""", (artist_id,))
        conn.commit()
    return redirect(url_for("artist", artist_id=artist_id))


def _enrich_all(conn, items):
    """Attach display context to pending items with one batched lookup per
    entity type (was 2-4 SELECTs per item — N+1 on every queue load)."""
    attach = [i for i in items if i["kind"] == "one_directional_attach"]
    merges = [i for i in items if i["kind"] == "cluster_merge"]
    gates = [i for i in items if i["kind"] == "singleton_gate"]

    acc_ids = ({i["payload"]["source_account_id"] for i in attach}
               | {i["payload"]["target_account_id"] for i in attach}
               | {i["payload"]["account_id"] for i in gates})
    # Live values — the payload snapshot goes stale (targets get hydrated
    # after the item was created).
    accounts = {r["id"]: r for r in q(conn, """
        select a.id, a.handle::text as handle, a.display_name, a.followers_count,
               a.avatar_url, a.profile_url, a.discovered_via, p.slug as platform
        from accounts a join platforms p on p.id = a.platform_id
        where a.id = any(%s)""", (list(acc_ids),))} if acc_ids else {}
    src_artists = {r["account_id"]: r for r in q(conn, """
        select aa.account_id, ar.public_slug, ar.id as artist_id
        from artist_accounts aa join artists ar on ar.id = aa.artist_id
        where aa.removed_at is null and aa.account_id = any(%s)""",
        (list({i["payload"]["source_account_id"] for i in attach}),))} if attach else {}
    edges = {r["id"]: r for r in q(conn, """
        select id, evidence_type, evidence_url, matched_text
        from identity_edges where id = any(%s)""",
        (list({i["payload"]["edge_id"] for i in attach}),))} if attach else {}

    # Anomaly groups get the same artist chip (pfp + slug) as merge cards.
    anomaly_artist_ids = {i["payload"]["artist_id"] for i in items
                          if i["kind"] == "other"
                          and (i["payload"] or {}).get("artist_id")}
    merge_ids = sorted({aid for i in merges
                        for aid in json.loads(i["payload"]["artist_ids"])}
                       | anomaly_artist_ids)
    merge_artist = {r["id"]: r for r in q(conn, """
        select ar.id, ar.public_slug,
               (select coalesce(nullif(a.display_name, ''), a.handle::text)
                from artist_accounts aa
                join accounts a on a.id = aa.account_id
                join platforms p on p.id = a.platform_id
                where aa.artist_id = ar.id and aa.removed_at is null
                  and a.status in ('active', 'unknown')
                order by p.display_rank, a.followers_count desc nulls last
                limit 1) as display_name,
               (select max(a.followers_count) from artist_accounts aa
                join accounts a on a.id = aa.account_id
                where aa.artist_id = ar.id and aa.removed_at is null) as followers,
               (select a.avatar_url from artist_accounts aa
                join accounts a on a.id = aa.account_id
                join platforms p on p.id = a.platform_id
                where aa.artist_id = ar.id and aa.removed_at is null
                  and a.avatar_url is not null
                  and a.status in ('active', 'unknown')
                order by p.display_rank, a.followers_count desc nulls last
                limit 1) as avatar_url
        from artists ar where ar.id = any(%s)""", (merge_ids,))} if merge_ids else {}

    bios = {r["account_id"]: r["bio_text"] for r in q(conn, """
        select distinct on (account_id) account_id, bio_text
        from account_snapshots where account_id = any(%s)
        order by account_id, captured_at desc""",
        ([i["payload"]["account_id"] for i in gates],))} if gates else {}

    for item in items:
        payload = item["payload"]
        ctx = {}
        if item["kind"] == "one_directional_attach":
            src = src_artists.get(payload["source_account_id"])
            src_acc = accounts.get(payload["source_account_id"])
            tgt = accounts.get(payload["target_account_id"])
            edge = edges.get(payload["edge_id"])
            if src and tgt:
                ctx = {"artist_id": src["artist_id"], "artist_slug": src["public_slug"],
                       "source_handle": src_acc["handle"] if src_acc else None,
                       "target_handle": tgt["handle"],
                       "target_platform": tgt["platform"],
                       "target_url": tgt["profile_url"],
                       "target_avatar": tgt["avatar_url"],
                       "target_followers": tgt["followers_count"],
                       "evidence_type": edge["evidence_type"] if edge else None,
                       "matched_text": edge["matched_text"] if edge else None,
                       "evidence_url": edge["evidence_url"] if edge else None,
                       "reason": payload.get("reason") or payload.get("evidence")}
        elif item["kind"] == "cluster_merge":
            ids = json.loads(payload["artist_ids"])
            artists = [merge_artist[a] for a in sorted(ids) if a in merge_artist]
            # WHAT connects them: every present edge whose endpoints sit in
            # different artists of this pair (pair-scoped, stays per-item).
            # `mutual` marks a direct reverse edge between the same two
            # accounts — a two-way claim is far stronger merge evidence than
            # a lone one-directional link, so the UI highlights the split.
            evidence = q(conn, """
                select e.evidence_type, e.claim, e.evidence_url, e.matched_text,
                       sa.handle::text as src_handle, sp.slug as src_platform,
                       sa.profile_url as src_url, sa.display_name as src_name,
                       ta.handle::text as tgt_handle, tp.slug as tgt_platform,
                       ta.profile_url as tgt_url, ta.display_name as tgt_name,
                       exists (select 1 from identity_edges r
                               where r.status = 'present'
                                 and r.source_account_id = e.target_account_id
                                 and r.target_account_id = e.source_account_id
                       ) as mutual
                from identity_edges e
                join artist_accounts saa on saa.account_id = e.source_account_id
                     and saa.removed_at is null and saa.artist_id = any(%(ids)s)
                join artist_accounts taa on taa.account_id = e.target_account_id
                     and taa.removed_at is null and taa.artist_id = any(%(ids)s)
                join accounts sa on sa.id = e.source_account_id
                join platforms sp on sp.id = sa.platform_id
                join accounts ta on ta.id = e.target_account_id
                join platforms tp on tp.id = ta.platform_id
                where e.status = 'present' and saa.artist_id <> taa.artist_id
                order by (exists (select 1 from identity_edges r
                                  where r.status = 'present'
                                    and r.source_account_id = e.target_account_id
                                    and r.target_account_id = e.source_account_id)) desc
                limit 8""", {"ids": ids})
            ctx = {"artists": artists, "evidence": evidence,
                   "any_mutual": any(ev["mutual"] for ev in evidence),
                   "keeper_slug": artists[0]["public_slug"] if artists else "?"}
        elif item["kind"] == "singleton_gate":
            ctx = {"bio": bios.get(payload["account_id"]),
                   "account": accounts.get(payload["account_id"])}
        elif item["kind"] == "other" and (payload or {}).get("artist_id"):
            ctx = {"artist": merge_artist.get(payload["artist_id"])}
        item["ctx"] = ctx
    return items


@app.route("/review")
def review():
    with db.connect() as conn:
        items = _enrich_all(conn, q(
            conn, "select * from review_items where status = 'pending' order by created_at"))
        merge_items = [i for i in items if i["kind"] == "cluster_merge"]
        # ALL 'other' items (anomalies + giant components) are informational:
        # nothing structural happens on decision, so they live in the anomaly
        # section with acknowledge/dismiss wording, never an "Approve" that
        # reads like it merges something.
        anomaly_items = [i for i in items if i["kind"] == "other"]
        # One artist often trips several anomaly rules (hub fanout + a couple
        # of cross-artist-ref accounts) — show ONE card per artist with every
        # flag inside; its buttons decide all grouped items at once.
        by_artist: dict = {}
        anomaly_groups = []
        for item in anomaly_items:
            payload = item["payload"] or {}
            aid = payload.get("artist_id") if payload.get("type") == "anomaly" else None
            if aid is None:  # giant components etc. stay individual cards
                anomaly_groups.append({"public_slug": None, "items": [item]})
                continue
            if aid not in by_artist:
                by_artist[aid] = {"artist_id": aid,
                                  "public_slug": payload.get("public_slug"),
                                  "artist": (item["ctx"] or {}).get("artist"),
                                  "items": []}
                anomaly_groups.append(by_artist[aid])
            by_artist[aid]["items"].append(item)
        # "Unlikely artists": open-harvest accounts that failed the evidence
        # gate — their own section, distinct from the (legacy, near-drained)
        # one-directional attach decisions.
        gate_items = [i for i in items if i["kind"] == "singleton_gate"]
        attach_items = [i for i in items
                        if i["kind"] not in ("cluster_merge", "other", "singleton_gate")]
        return render_template("review.html", merge_items=merge_items,
                               anomaly_groups=anomaly_groups,
                               anomaly_count=len(anomaly_items),
                               gate_items=gate_items[:80],
                               gate_total=len(gate_items),
                               attach_items=attach_items[:60],
                               attach_total=len(attach_items),
                               pending=len(items), demoted_count=demoted_count(conn))


def _approve(conn, item):
    payload = item["payload"]
    with conn.cursor() as cur:
        if item["kind"] == "one_directional_attach":
            cur.execute("""select 1 from artist_accounts
                           where account_id = %s and removed_at is null""",
                        (payload["target_account_id"],))
            if cur.fetchone() is None:
                cur.execute("""insert into artist_accounts (artist_id, account_id, confidence, added_by)
                               values (%s, %s, 'strong', 'human')""",
                            (payload["artist_id"], payload["target_account_id"]))
                cur.execute("""insert into artist_events (artist_id, event, actor, details)
                               values (%s, 'account_added', 'admin:review-ui', %s)""",
                            (payload["artist_id"], json.dumps(
                                {"account_id": payload["target_account_id"],
                                 "edge_id": payload["edge_id"]})))
        elif item["kind"] == "singleton_gate":
            from .cluster import create_artist

            account = q(conn, """
                select a.id, a.handle::text, a.display_name, a.followers_count,
                       p.slug as platform_slug
                from accounts a join platforms p on p.id = a.platform_id
                where a.id = %s""", (payload["account_id"],))
            cur.execute("""select 1 from artist_accounts
                           where account_id = %s and removed_at is null""",
                        (payload["account_id"],))
            if account and cur.fetchone() is None:
                create_artist(conn, account[0], actor="admin:review-ui")
        elif item["kind"] == "cluster_merge":
            from .cluster import merge_artists

            ids = sorted(json.loads(payload["artist_ids"]))
            live = [r["id"] for r in q(conn, """
                select id from artists
                where id = any(%s) and merged_into is null""", (ids,))]
            # Fewer than two live artists left: the pair already merged (or
            # collapsed) some other way — resolving the item is all that's left.
            if len(live) >= 2:
                merge_artists(conn, live[0], live[1:], actor="admin:review-ui")


def _decide_one(conn, item_id: int, decision: str) -> None:
    item = q(conn, "select * from review_items where id = %s and status = 'pending'", (item_id,))
    if not item:
        return
    if decision == "approve":
        _approve(conn, item[0])
    with conn.cursor() as cur:
        cur.execute("""update review_items
                       set status = %s, resolved_at = now(), decided_by = 'admin:review-ui'
                       where id = %s""",
                    ("approved" if decision == "approve" else "rejected", item_id))


@app.route("/review/<int:item_id>/<decision>", methods=["POST"])
def decide(item_id, decision):
    assert decision in ("approve", "reject")
    with db.connect() as conn:
        _decide_one(conn, item_id, decision)
        conn.commit()
    # Artist pages embed decide forms with next=<their own path> so a
    # decision made mid-inspection lands back on the page being inspected.
    nxt = request.form.get("next", "")
    if not (nxt.startswith("/") and not nxt.startswith("//")):
        nxt = url_for("review")
    return redirect(nxt)


@app.route("/review/bulk", methods=["POST"])
def bulk_decide():
    decision = request.form.get("decision")
    assert decision in ("approve", "reject")
    with db.connect() as conn:
        # Grouped anomaly cards submit their item ids comma-joined in one value.
        for field in request.form.getlist("items"):
            for item_id in field.split(","):
                _decide_one(conn, int(item_id), decision)
        conn.commit()
    return redirect(url_for("review"))


# ---------------------------------------------------------------------------
# x-tag extension API (see xtag/ in the repo root). All endpoints require the
# X-Inkpages-Token header (checked in _csrf_protect). One-click tags create
# handle-only twitter accounts with discovered_via='manual_tag' — a roster
# source, so after paid hydration + a cluster run they list as singleton
# artists. Tagging never spends: hydration happens on the explicit /flush.
# ---------------------------------------------------------------------------

_X_HANDLE_RE = re.compile(r"^[a-z0-9_]{1,15}$")
_STATE_RANK = {"listed": 5, "tagged": 4, "queued": 3, "tracked": 2, "removed": 1}


# Hard sanity bound only — a bulk select-all over a big following list is a
# legitimate multi-thousand-handle request. NEVER silently truncate below it:
# an early raw[:500] cap once ate 2,000 of a 2,500-handle bulk add.
_MAX_HANDLES = 20_000


def _clean_handles(payload) -> list[str]:
    raw = (payload or {}).get("handles") or []
    if len(raw) > _MAX_HANDLES:
        abort(413)
    seen, out = set(), []
    for h in raw:
        h = str(h).strip().lstrip("@").lower()
        if _X_HANDLE_RE.match(h) and h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _x_states(conn, handles: list[str]) -> dict[str, dict]:
    """Directory state per handle: untracked | queued | tracked | listed |
    removed. A handle can match several rows (native-id + handle-only dupes);
    the strongest state wins."""
    if not handles:
        return {}
    rows = q(conn, """
        select lower(a.handle::text) as h, a.id as account_id, a.status,
               a.discovered_via, a.project,
               a.last_hydrated is not null as hydrated,
               ar.id as artist_id, ar.public_slug, ar.status as artist_status,
               exists (select 1 from directory_entries de
                       where de.artist_id = ar.id) as listed,
               exists (select 1 from suppressions s where s.lifted_at is null
                       and (s.account_id = a.id or s.artist_id = ar.id)) as suppressed
        from accounts a
        join platforms p on p.id = a.platform_id and p.slug = 'twitter'
        left join artist_accounts aa on aa.account_id = a.id and aa.removed_at is null
        left join artists ar on ar.id = aa.artist_id and ar.merged_into is null
        where lower(a.handle::text) = any(%(hs)s)""", {"hs": handles})
    out: dict[str, dict] = {}
    for r in rows:
        if r["suppressed"]:
            state, detail = "removed", "suppressed"
        elif r["status"] == "hidden":
            state, detail = "removed", "hidden (culled)"
        elif r["status"] == "deleted":
            state, detail = "removed", "deleted on X"
        elif r["discovered_via"] == "manual_tag" and not r["hydrated"]:
            # Queued means queued until the paid flush hydrates it — even if
            # a cluster pass has already attached it somewhere. The extension
            # shows amber here; green is reserved for hydrated states.
            state, detail = "queued", None
        elif r["listed"]:
            state, detail = "listed", None
        elif r["discovered_via"] == "manual_tag":
            # Hydrated tag awaiting its cluster pass (or held from listing).
            state, detail = "tagged", (f"artist {r['artist_status']}"
                                       if r["artist_id"] else "awaiting cluster run")
        elif r["artist_id"]:
            state, detail = "tracked", f"artist {r['artist_status']}"
        else:
            state, detail = "tracked", ("project account" if r["project"]
                                        else r["discovered_via"])
        cand = {"state": state, "detail": detail,
                "artist_id": r["artist_id"], "slug": r["public_slug"]}
        prev = out.get(r["h"])
        if prev is None or _STATE_RANK[state] > _STATE_RANK[prev["state"]]:
            out[r["h"]] = cand
    for h in handles:
        out.setdefault(h, {"state": "untracked", "detail": None,
                           "artist_id": None, "slug": None})
    return out


@app.route("/api/x/status", methods=["POST"])
def api_x_status():
    handles = _clean_handles(request.get_json(silent=True))
    with db.connect() as conn:
        return {"accounts": _x_states(conn, handles)}


@app.route("/api/x/tag", methods=["POST"])
def api_x_tag():
    payload = request.get_json(silent=True) or {}
    handles = _clean_handles(payload)
    referrer = str(payload.get("referrer") or "")[:300] or None
    done: dict[str, dict] = {}
    with db.connect() as conn:
        twitter_id = db.platform_ids(conn)["twitter"]
        states = _x_states(conn, handles)
        with conn.cursor() as cur:
            for h in handles:
                info = states[h]
                if info["state"] in ("listed", "queued", "tagged"):
                    done[h] = info  # already in — idempotent
                    continue
                if info["state"] == "removed" and info["detail"] == "suppressed":
                    # Suppressions persist by design (opt-out rule); lifting
                    # them stays a deliberate review-UI action.
                    done[h] = {**info, "note": "suppressed — lift in review UI"}
                    continue
                account_id = db.get_or_create_account(
                    conn, twitter_id, handle=h,
                    profile_url=f"https://x.com/{h}",
                    discovered_via="manual_tag",
                    discovery_details={"source": "x-tag", "referrer": referrer})
                # Adopt pre-existing rows as human tags: promote incidental
                # discovery to the roster source, and lift hidden/deleted
                # states (a tag IS the explicit admin action a cull demands).
                # Roster/harvest discovered_via values are left alone.
                cur.execute(
                    """update accounts
                       set discovered_via = case
                             when discovered_via in ('bio_link', 'bio_mention',
                                                     'link_hub', 'hydration')
                             then 'manual_tag' else discovered_via end,
                           status = case
                             when status in ('hidden', 'deleted') then
                               case when last_hydrated is null
                                    then 'unknown' else 'active' end
                             else status end,
                           discovery_details = coalesce(discovery_details, '{}'::jsonb)
                               || jsonb_build_object('x_tagged_at', now()::text)
                       where id = %s""", (account_id,))
        conn.commit()
        pending = [h for h in handles if h not in done]
        done.update(_x_states(conn, pending))
    return {"accounts": done}


def _untag_handles(conn, handles: list[str]) -> dict[str, dict]:
    """Shared by the extension API and the /xtag dashboard bulk form."""
    states = _x_states(conn, handles)
    with conn.cursor() as cur:
            for h in handles:
                info = states[h]
                if info["state"] == "listed":
                    # Removing a listed profile = suppressing the artist
                    # (persists against re-discovery; reversible from the
                    # review UI's Removed page).
                    cur.execute(
                        """insert into suppressions (artist_id, reason, note, requested_by)
                           values (%s, 'other', 'x-tag removal', 'admin:x-tag')""",
                        (info["artist_id"],))
                    cur.execute(
                        """insert into artist_events (artist_id, event, actor, details)
                           values (%s, 'suppressed', 'admin:x-tag', %s)""",
                        (info["artist_id"],
                         json.dumps({"reason": "x-tag removal", "handle": h})))
                elif info["state"] in ("queued", "tracked", "tagged"):
                    # A tag-only row with no history vanishes outright;
                    # anything with snapshots/edges/memberships hides instead
                    # (data kept, directory-invisible).
                    cur.execute(
                        """delete from accounts a using platforms p
                           where p.id = a.platform_id and p.slug = 'twitter'
                             and lower(a.handle::text) = %s
                             and a.discovered_via = 'manual_tag'
                             and not exists (select 1 from account_snapshots s
                                             where s.account_id = a.id)
                             and not exists (select 1 from identity_edges e
                                             where e.source_account_id = a.id
                                                or e.target_account_id = a.id)
                             and not exists (select 1 from artist_accounts aa
                                             where aa.account_id = a.id)""", (h,))
                    if cur.rowcount == 0:
                        cur.execute(
                            """update accounts a set status = 'hidden'
                               from platforms p
                               where p.id = a.platform_id and p.slug = 'twitter'
                                 and lower(a.handle::text) = %s
                                 and a.status <> 'deleted'""", (h,))
    conn.commit()
    return _x_states(conn, handles)


@app.route("/api/x/untag", methods=["POST"])
def api_x_untag():
    handles = _clean_handles(request.get_json(silent=True))
    with db.connect() as conn:
        return {"accounts": _untag_handles(conn, handles)}


_X_QUEUE_SQL = """
    select a.handle::text as handle from accounts a
    join platforms p on p.id = a.platform_id and p.slug = 'twitter'
    where a.discovered_via = 'manual_tag' and a.last_hydrated is null
      and a.native_id is null and a.status = 'unknown'
    order by a.id"""


@app.route("/api/x/queue")
def api_x_queue():
    from .twitter import USER_READ_CENTS, spend_cap_cents, spent_cents

    with db.connect() as conn:
        n = len(q(conn, _X_QUEUE_SQL))
        return {"queued": n,
                "est_cents": int(n * USER_READ_CENTS),
                "spent_cents": spent_cents(conn),
                "cap_cents": spend_cap_cents()}


@app.route("/api/x/flush", methods=["POST"])
def api_x_flush():
    """Hydrate the manual-tag queue NOW (paid; the popup shows the exact cost
    on the button, so the click is the spend approval). Optionally kicks off a
    background pipeline run so the new artists actually list."""
    from .twitter import (USER_READ_CENTS, XApi, process_user,
                          spend_cap_cents, spent_cents)

    if not db.env_var("X_API_BEARER_TOKEN"):
        return {"error": "X_API_BEARER_TOKEN not set on the server"}, 400
    payload = request.get_json(silent=True) or {}
    stats: Counter = Counter()
    with db.connect() as conn:
        handles = [r["handle"] for r in q(conn, _X_QUEUE_SQL)]
        if not handles:
            return {"hydrated": 0, "missing": 0, "cost_cents": 0,
                    "pipeline_started": False, "message": "queue empty"}
        planned = len(handles) * USER_READ_CENTS
        spent, cap = spent_cents(conn), spend_cap_cents()
        if spent + planned > cap:
            return {"error": f"budget cap: {spent}c spent + {planned:.0f}c "
                             f"planned exceeds {cap}c"}, 400
        api = XApi(conn)  # ledgers each batch into api_usage as it returns
        found, missing = api.users_by(handles, note="x-tag flush")
        platforms = db.platform_ids(conn)
        for user in found:
            process_user(conn, platforms, user, "hydration", {}, stats)
        with conn.cursor() as cur:
            for h in missing:
                cur.execute(
                    """update accounts set status = 'deleted', last_hydrated = now()
                       where platform_id = %s and handle = %s and native_id is null""",
                    (platforms["twitter"], h))
        conn.commit()
    pipeline_started = bool(payload.get("run_pipeline"))
    if pipeline_started:
        # Free stages only (crawl → cluster → classify). Output appends to
        # the xtag pipeline log so /xtag can show live progress.
        with open(_XTAG_LOG, "ab") as log:
            log.write(f"\n=== pipeline started {time.strftime('%Y-%m-%d %H:%M:%S')} "
                      f"(after flush of {len(found)}) ===\n".encode())
            subprocess.Popen([sys.executable, "-m", "inkpages.pipeline"],
                             stdout=log, stderr=subprocess.STDOUT)
    return {"hydrated": len(found), "missing": len(missing),
            "cost_cents": int(planned), "pipeline_started": pipeline_started}


_XTAG_LOG = db.ROOT / "xtag-pipeline.log"


def _running_workers() -> list[str]:
    """Pipeline-family python processes currently alive (whoever started
    them). Only actual `python -m inkpages.X` processes count — shell
    wrappers whose command STRING mentions a worker name must not."""
    try:
        # NB: the pattern must not start with a dash (pgrep eats it as a flag).
        out = subprocess.run(
            ["pgrep", "-fl", r"inkpages\.(pipeline|crawl_links|check_links|"
             r"cluster|classify_region|hydrate_twitter|discover_)"],
            capture_output=True, text=True, timeout=5).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    workers = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or parts[1].rstrip(":").endswith(("sh", "zsh", "bash")):
            continue
        if m := re.search(r"-m\s+(inkpages\.\w+)", line):
            workers.append(m.group(1))
    return sorted(set(workers))


@app.route("/xtag")
def xtag_dashboard():
    from .twitter import USER_READ_CENTS, spend_cap_cents, spent_cents

    state = request.args.get("state", "")
    query = request.args.get("q", "").strip()
    page = max(int(request.args.get("page", "1") or 1), 1)
    per = 100
    where = ["a.discovered_via = 'manual_tag'", "p.slug = 'twitter'"]
    params: dict = {}
    if state == "queued":
        where.append("a.last_hydrated is null and a.status = 'unknown'")
    elif state == "listed":
        where.append("de.artist_id is not null")
    elif state == "hydrated":
        where.append("a.last_hydrated is not null and de.artist_id is null "
                     "and a.status not in ('hidden', 'deleted')")
    elif state == "removed":
        where.append("a.status in ('hidden', 'deleted')")
    if query:
        where.append("(a.handle::text ilike %(q)s or a.display_name ilike %(q)s)")
        params["q"] = f"%{query}%"
    base_sql = f"""
        from accounts a
        join platforms p on p.id = a.platform_id
        left join artist_accounts aa on aa.account_id = a.id and aa.removed_at is null
        left join artists ar on ar.id = aa.artist_id and ar.merged_into is null
        left join directory_entries de on de.artist_id = ar.id
        where {' and '.join(where)}"""
    with db.connect() as conn:
        total = q(conn, f"select count(*) n {base_sql}", params)[0]["n"]
        rows = q(conn, f"""
            select a.id, a.handle::text as handle, a.display_name, a.avatar_url,
                   a.followers_count, a.last_hydrated, a.status,
                   ar.id as artist_id, ar.public_slug,
                   de.artist_id is not null as listed
            {base_sql}
            order by a.id desc limit {per} offset {(page - 1) * per}""", params)
        stats = q(conn, """
            select count(*) as tagged,
                   count(*) filter (where a.last_hydrated is null
                                    and a.status = 'unknown') as queued,
                   count(*) filter (where a.last_hydrated is not null
                                    and a.status not in ('hidden','deleted')) as hydrated,
                   count(distinct aa.artist_id)
                       filter (where aa.removed_at is null) as artists,
                   count(*) filter (where a.status in ('hidden','deleted')) as removed
            from accounts a
            join platforms p on p.id = a.platform_id and p.slug = 'twitter'
            left join artist_accounts aa on aa.account_id = a.id
            where a.discovered_via = 'manual_tag'""")[0]
        hubs_uncrawled = cached("xtag_hubs", lambda: q(conn, """
            select count(*) n from accounts h
            join platforms p on p.id = h.platform_id
            where p.kind = 'link_hub' and h.last_hydrated is null
              and h.status <> 'deleted'""")[0]["n"])
        flushes = q(conn, """
            select occurred_at, sum(units) units, sum(est_cost_cents) cents
            from api_usage where service = 'x_api' and note = 'x-tag flush'
            group by occurred_at order by occurred_at desc limit 8""")
        spent, cap = spent_cents(conn), spend_cap_cents()
        pending = pending_count(conn)
        demoted = demoted_count(conn)
    workers = _running_workers()
    log_tail = ""
    if _XTAG_LOG.exists():
        with open(_XTAG_LOG, "rb") as fh:
            fh.seek(max(fh.seek(0, 2) - 6000, 0))
            log_tail = fh.read().decode(errors="replace")[-6000:]
    pages = max((total + per - 1) // per, 1)
    return render_template(
        "xtag.html", rows=rows, stats=stats, total=total, page=page, pages=pages,
        page_window=page_window(page, pages), state=state, qtext=query,
        workers=workers, log_tail=log_tail, hubs_uncrawled=hubs_uncrawled,
        flushes=flushes, spent=spent, cap=cap,
        queue_cents=int(stats["queued"] * USER_READ_CENTS),
        pending=pending, demoted_count=demoted)


@app.route("/xtag/remove", methods=["POST"])
def xtag_remove():
    handles = _clean_handles({"handles": request.form.getlist("handles")})
    with db.connect() as conn:
        results = _untag_handles(conn, handles)
    removed = sum(1 for v in results.values() if v["state"] in ("removed", "untracked"))
    return redirect(url_for("xtag_dashboard", state=request.form.get("back_state") or None,
                            q=request.form.get("back_q") or None,
                            msg=f"removed {removed} of {len(handles)}"))


def main():
    print("x-tag API token:", tag_token())
    # INKPAGES_HOST: default loopback-only. Set it to your Tailscale IP (or
    # 0.0.0.0 behind a trusted private network ONLY) so the x-tag extension
    # on another machine can reach the API. The HTML admin routes have CSRF
    # but no login — never bind to anything internet-reachable.
    host = db.env_var("INKPAGES_HOST") or "127.0.0.1"
    if host != "127.0.0.1":
        print(f"WARNING: binding {host} — admin UI is reachable by anything "
              "that can route here; keep this to a private tailnet/VPN")
    # threaded: a bulk x-tag write must not block status lookups / page loads
    # (each request opens its own DB connection, so this is safe).
    app.run(host=host, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
