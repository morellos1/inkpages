"""Local review UI for the directory: browse artists, inspect evidence,
work the review queue, suppress/unsuppress.

Usage: uv run python -m inkpages.review_ui   (then open http://127.0.0.1:8322)
Local admin tooling — binds to 127.0.0.1 only.
"""
import json

from flask import Flask, redirect, render_template, request, url_for
from jinja2 import DictLoader
from psycopg.rows import dict_row

from . import db

PORT = 8322

TEMPLATES = {
"base.html": """<!doctype html><html><head><meta charset="utf-8">
<title>inkpages review</title>
<style>
  body { font: 15px/1.5 -apple-system, system-ui, sans-serif; margin: 0; color: #1a1a1a; background: #fafafa; }
  header { background: #14213d; color: #fff; padding: .7rem 1.2rem; display: flex; gap: 1.4rem; align-items: baseline; }
  header a { color: #cdd7ee; text-decoration: none; } header a:hover { color: #fff; }
  header .brand { font-weight: 700; color: #fff; }
  header .pill { background: #fca311; color: #14213d; border-radius: 9px; padding: 0 .5em; font-size: .82em; font-weight: 700; }
  main { max-width: 1150px; margin: 1.4rem auto; padding: 0 1.2rem; }
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
  .bio { background: #f6f6f2; border-left: 3px solid #cbd5e1; padding: .4rem .7rem; white-space: pre-wrap; font-size: .92em; }
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
</style></head><body>
<header>
  <a class="brand" href="{{ url_for('index') }}">inkpages review</a>
  <a href="{{ url_for('index') }}">Directory</a>
  <a href="{{ url_for('review') }}">Review queue {% if pending %}<span class="pill">{{ pending }}</span>{% endif %}</a>
  <a href="{{ url_for('demoted') }}">Demoted {% if demoted_count %}<span class="pill">{{ demoted_count }}</span>{% endif %}</a>
</header>
<main>{% block content %}{% endblock %}</main>
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
  {% else %}
    {% for k, v in s.items() if v is not none %}<span class="stat-chip">{{ k }}: {{ v }}</span>{% endfor %}
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
{% set any_filter = sel_platforms or sel_langs or sel_flags or sel_sources or sel_comms %}
{% if q or any_filter %}<a class="linkish" href="{{ url_for('index') }}" style="margin-left:.6rem">clear all</a>{% endif %}</p>
<details class="filters" {% if any_filter %}open{% endif %}>
  <summary>Filters{% set n = sel_platforms|length + sel_langs|length + sel_flags|length + sel_sources|length + sel_comms|length %}{% if n %} <span class="pill">{{ n }}</span>{% endif %}</summary>
  <div class="filter-body">
    <div class="facet"><div class="facet-label">Flags</div><div class="facet-opts">
      {% for val, lbl in flag_labels %}
      <label><input type="checkbox" name="flag" value="{{ val }}" {% if val in sel_flags %}checked{% endif %}>{{ lbl }}</label>
      {% endfor %}
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
<table><tr><th></th>{{ sorth('artist','artist') }}{{ sorth('lang','lang') }}{{ sorth('followers','followers') }}<th>accounts</th><th>flags</th></tr>
{% for a in artists %}<tr>
  <td>{% if a.avatar_url %}<img src="{{ img_src(a.avatar_url) }}" width="36" height="36" style="border-radius:50%;object-fit:cover" loading="lazy">{% endif %}</td>
  {# When the slug is an opaque pixiv id, lead with the human name. #}
  {%- set id_slug = a.public_slug.isdigit() and a.display_name -%}
  <td><a href="{{ url_for('artist', artist_id=a.artist_id) }}"><b>{{ a.display_name if id_slug else a.public_slug }}</b></a><br>
      <span class="muted">{{ ('/' ~ a.public_slug) if id_slug else a.display_name }}</span></td>
  <td>{{ a.language }}</td>
  <td>{{ "{:,}".format(a.followers) if a.followers else "—" }}</td>
  <td>{% for s in a.sources or [] %}<span class="chip badge-noai" style="background:#e8edf7;color:#14213d">{{ s }}</span>{% endfor %}
      {% for acc in a.accounts or [] %}<span class="chip">{{ acc.platform }}: {{ acc_label(acc.platform, acc.handle, acc.display_name) }}</span>{% endfor %}</td>
  {%- set plats = (a.accounts or [])|map(attribute='platform')|list -%}
  <td>{% if a.no_ai_attested %}<span class="chip badge-noai">no-AI</span>{% endif %}
      {% if a.nsfw %}<span class="chip badge-nsfw">18+</span>{% endif %}
      {% if 'twitter' not in plats and 'bluesky' not in plats %}<span class="chip badge-suppressed">no X/bsky</span>{% endif %}
      {% if a.dormant %}<span class="chip badge-dormant">dormant</span>{% endif %}
      {% if a.commissions %}<span class="chip badge-{{ a.commissions.status }}"
        title="confidence {{ a.commissions.confidence }}">comms {{ a.commissions.status }}
        · {{ a.commissions.checked_at[:10] }}</span>{% endif %}</td>
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
  <form class="inline" method="post" action="{{ url_for('unsuppress', artist_id=artist.id) }}">
    <button class="ok">Lift suppression</button>
    <span class="muted">currently: {{ suppressed.reason }} — {{ suppressed.note or "" }}</span></form>
{% else %}
  <form class="inline" method="post" action="{{ url_for('suppress', artist_id=artist.id) }}">
    <select name="reason"><option>opt_out</option><option>impersonation</option><option>ai_use_confirmed</option><option>other</option></select>
    <input type="text" name="note" placeholder="note">
    <button class="no">Suppress (remove from directory)</button></form>
{% endif %}
</div>

<h2>Accounts</h2>
<table><tr><th>platform</th><th>handle</th><th>confidence</th><th>followers</th><th>last post</th><th>comms</th><th>contact</th><th>bio (latest snapshot)</th><th></th></tr>
{% for acc in accounts %}<tr>
  <td>{{ acc.platform }}</td>
  <td>{{ m.acct_link(acc.platform, acc.handle, acc.profile_url, acc.display_name) }}
      {{ m.stats(acc.platform, acc.platform_stats) }}</td>
  <td class="conf-{{ acc.confidence }}">{{ acc.confidence }}</td>
  <td>{{ "{:,}".format(acc.followers_count) if acc.followers_count else "—" }}</td>
  <td>{{ acc.last_post_at.date() if acc.last_post_at else "—" }}</td>
  <td>{% if acc.commission_status != 'unknown' %}<span class="chip badge-{{ acc.commission_status }}"
        title="{{ acc.commission_detail }}">{{ acc.commission_status }}
        · {{ acc.commission_checked_at.date() if acc.commission_checked_at }}</span>{% else %}—{% endif %}</td>
  <td>{{ acc.contact_email or "—" }}</td>
  <td>{{ m.bio(acc.bio) }}</td>
  <td><form class="inline" method="post" action="{{ url_for('detach', artist_id=artist.id, account_id=acc.id) }}"
       onsubmit="return confirm('Detach {{ acc.handle }} from this artist? It becomes a connection and will never auto-reattach.')">
       <button class="no">detach</button></form></td>
</tr>{% endfor %}</table>

<h2>Connections (related, never merged)</h2>
<table><tr><th>direction</th><th>account</th><th>followers</th><th>hint</th><th>evidence</th><th></th></tr>
{% for c in connections %}<tr>
  <td>{{ c.direction }}</td>
  <td><span class="chip">{{ c.other_platform }}: {{ m.acct_link(c.other_platform, c.other_handle, c.other_profile_url, c.other_display_name) }}</span></td>
  <td>{{ "{:,}".format(c.other_followers) if c.other_followers else "—" }}</td>
  <td>{{ c.relation_hint or "—" }}</td>
  <td class="muted">{{ c.matched_text or c.evidence_url or "" }}</td>
  <td><form class="inline" method="post" action="{{ url_for('confirm_connection', artist_id=artist.id, account_id=c.other_id) }}"
       onsubmit="return confirm('Confirm {{ c.other_platform }}:{{ c.other_handle }} as the same person and attach it to this artist?')">
       <button class="ok">confirm</button></form></td>
</tr>{% else %}<tr><td colspan="6" class="muted">none</td></tr>{% endfor %}</table>

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
<td>{{ e.event }}</td><td>{{ e.actor }}</td><td class="muted">{{ e.details }}</td></tr>{% endfor %}</table>
{% endblock %}""",

"demoted.html": """{% extends "base.html" %}{% import "_macros.html" as m %}{% block content %}
<h1>Demoted (no artist evidence)</h1>
<p class="muted">Open-harvest accounts that failed the artist-evidence test. Restore
puts an artist back in the directory and permanently exempts them from auto-demotion.</p>
{% if not items %}<p class="muted">Nothing here.</p>{% endif %}
<table>{% if items %}<tr><th>artist</th><th>followers</th><th>bio</th><th></th></tr>{% endif %}
{% for a in items %}<tr>
  <td><a href="{{ url_for('artist', artist_id=a.id) }}"><b>{{ a.public_slug }}</b></a><br>
      <span class="muted">{{ a.display_name }}</span></td>
  <td>{{ "{:,}".format(a.followers) if a.followers else "—" }}</td>
  <td>{{ m.bio(a.bio) }}</td>
  <td><form class="inline" method="post" action="{{ url_for('restore', artist_id=a.id) }}">
      <button class="ok">Restore</button></form></td>
</tr>{% endfor %}</table>
{% endblock %}""",

"review.html": """{% extends "base.html" %}{% import "_macros.html" as m %}{% block content %}
{% macro decide_buttons(item) %}
  <label class="muted"><input type="checkbox" name="items" value="{{ item.id }}" form="bulk"> select</label>
  <form class="inline" method="post" action="{{ url_for('decide', item_id=item.id, decision='approve') }}"><button class="ok">Approve</button></form>
  <form class="inline" method="post" action="{{ url_for('decide', item_id=item.id, decision='reject') }}"><button class="no">Reject</button></form>
{% endmacro %}
<form id="bulk" method="post" action="{{ url_for('bulk_decide') }}"></form>
<div class="card" style="position:sticky;top:0;z-index:5">
  <button type="button" onclick="document.querySelectorAll('input[name=items]').forEach(c=>c.checked=true)">Select all</button>
  <button type="button" onclick="document.querySelectorAll('input[name=items]').forEach(c=>c.checked=false)">Clear</button>
  <button class="ok" form="bulk" name="decision" value="approve"
          onclick="return confirm('Approve all selected?')">Approve selected</button>
  <button class="no" form="bulk" name="decision" value="reject"
          onclick="return confirm('Reject all selected?')">Reject selected</button>
</div>
<h1>Merge decisions <span class="muted">({{ merge_items|length }})</span></h1>
{% if not merge_items %}<p class="muted">No merge decisions pending.</p>{% endif %}
{% for item in merge_items %}
<div class="card">
  <b>#{{ item.id }} · {{ item.kind }}</b> <span class="muted">{{ item.created_at.strftime('%Y-%m-%d %H:%M') }}</span>
  {% if item.kind == 'cluster_merge' %}
    <p>{% for a in item.ctx.artists %}<a href="{{ url_for('artist', artist_id=a.id) }}"><b>{{ a.public_slug }}</b></a>
       <span class="muted">({{ "{:,}".format(a.followers) if a.followers is not none else "? " }} followers)</span>{% if not loop.last %} + {% endif %}{% endfor %}</p>
    <p class="muted">Connecting evidence:</p>
    <ul class="muted">
    {% for ev in item.ctx.evidence %}
      <li>{{ ev.src_platform }}:{{ ev.src_handle }} → {{ ev.tgt_platform }}:{{ ev.tgt_handle }}
          — {{ ev.evidence_type }}{% if ev.claim == 'related' %} (related){% endif %}
          {% if ev.matched_text %} · “{{ ev.matched_text }}”{% endif %}
          {% if ev.evidence_url %} · <a href="{{ ev.evidence_url }}" target="_blank">source</a>{% endif %}</li>
    {% else %}<li>no live edges found (may already be resolved)</li>{% endfor %}
    </ul>
    <p class="muted">Approve = merge into <b>{{ item.ctx.keeper_slug }}</b>.</p>
  {% else %}<pre>{{ item.payload }}</pre>{% endif %}
  {{ decide_buttons(item) }}
</div>
{% endfor %}

<h1>Anomaly flags <span class="muted">({{ anomaly_items|length }})</span></h1>
{% if not anomaly_items %}<p class="muted">Nothing looks off.</p>{% endif %}
{% for item in anomaly_items %}
<div class="card">
  <b>#{{ item.id }}</b> ⚠️
  <a href="{{ url_for('artist', artist_id=item.payload.artist_id) }}"><b>{{ item.payload.public_slug }}</b></a>
  — link graph looks like a credits/projects page:
  {% for k, v in item.payload.reasons.items() %}<span class="chip badge-nsfw">{{ k }}: {{ v }}</span>{% endfor %}
  <p class="muted">Inspect the artist page; detach anything wrong there.
  Approve = looks fine as-is; Reject = dismissed.</p>
  {{ decide_buttons(item) }}
</div>
{% endfor %}

<h1>Attach decisions <span class="muted">({{ attach_total }})</span></h1>
{% for item in attach_items %}
<div class="card">
  <b>#{{ item.id }} · {{ item.ctx.reason or item.kind }}</b> <span class="muted">{{ item.created_at.strftime('%Y-%m-%d %H:%M') }}</span>
  {% if item.kind == 'one_directional_attach' %}
    <p><a href="{{ url_for('artist', artist_id=item.ctx.artist_id) }}">{{ item.ctx.artist_slug }}</a>
    ({{ item.ctx.source_handle }}) claims
    <b>{{ item.ctx.target_platform }}: {{ item.ctx.target_handle }}</b>
    ({% if item.ctx.target_followers is not none %}{{ "{:,}".format(item.ctx.target_followers) }} followers{% else %}followers unknown — not yet hydrated{% endif %})
    via {{ item.ctx.evidence_type }}{% if item.ctx.matched_text %} · “{{ item.ctx.matched_text }}”{% endif %}
    {% if item.ctx.evidence_url %} · <a href="{{ item.ctx.evidence_url }}" target="_blank">source</a>{% endif %}</p>
    <p class="muted">Approve = attach to this artist at strong confidence.</p>
  {% elif item.kind == 'singleton_gate' %}
    <p>Suspected non-artist from an open harvest: <b>{{ item.payload.platform }}: {{ item.payload.handle }}</b>
    ({{ "{:,}".format(item.payload.followers or 0) }} followers, via {{ item.payload.discovered_via }})</p>
    {{ m.bio(item.ctx.bio) }}
    <p class="muted">Approve = list as an artist (permanently exempt from auto-demotion).</p>
  {% else %}<pre>{{ item.payload }}</pre>{% endif %}
  {{ decide_buttons(item) }}
</div>
{% endfor %}
{% if attach_total > attach_items|length %}<p class="muted">…and {{ attach_total - attach_items|length }} more attach decisions (decide some to see the rest).</p>{% endif %}
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
FLAG_LABELS = [("no_ai", "no-AI"), ("nsfw", "18+"),
               ("dormant", "dormant"), ("no_pkey", "no X/bsky")]

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
SOURCE_OPTIONS = ["skeb", "bluesky", "twitter", "pixiv", "patreon"]

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
        r = httpx.get(url, timeout=10, follow_redirects=True,
                      headers={"Referer": "https://www.pixiv.net/",
                               "User-Agent": "Mozilla/5.0"})
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
        stats = q(conn, """
            select (select count(*) from directory_entries) as artists,
                   (select count(*) from directory_entries where no_ai_attested) as badged,
                   (select count(*) from directory_entries where nsfw) as nsfw,
                   (select count(*) from accounts) as accounts,
                   (select count(distinct artist_id) from suppressions where lifted_at is null) as suppressed""")[0]
        platform_options = [r["p"] for r in q(conn, """
            select distinct el->>'platform' as p
            from directory_entries de, jsonb_array_elements(de.accounts) el
            where el->>'platform' is not null order by 1""")]
        lang_options = [r["language"] for r in q(conn,
            "select distinct language from directory_entries order by 1")]
        return render_template("index.html", artists=artists, stats=stats, q=query,
                               sort=sort, dir=direction, sel_platforms=sel_platforms,
                               sel_langs=sel_langs, sel_flags=sel_flags, flag_labels=FLAG_LABELS,
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
                   (select s.bio_text from artist_accounts aa
                    join account_snapshots s on s.account_id = aa.account_id
                    where aa.artist_id = ar.id and aa.removed_at is null
                    order by s.captured_at desc limit 1) as bio
            from artists ar where ar.status = 'needs_review'
            order by followers desc nulls last""")
        return render_template("demoted.html", items=items,
                               pending=pending_count(conn), demoted_count=len(items))


@app.route("/artist/<int:artist_id>/restore", methods=["POST"])
def restore(artist_id):
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("update artists set status = 'active', updated_at = now() where id = %s",
                    (artist_id,))
        cur.execute("""insert into artist_events (artist_id, event, actor, details)
                       values (%s, 'unsuppressed', 'admin:review-ui',
                               '{"reason": "restored_from_demotion"}')""", (artist_id,))
        conn.commit()
    return redirect(url_for("demoted"))


@app.route("/artist/<int:artist_id>")
def artist(artist_id):
    with db.connect() as conn:
        artist = q(conn, "select * from artists where id = %s", (artist_id,))[0]
        accounts = q(conn, """
            select a.id, a.handle::text, a.display_name, a.profile_url, a.avatar_url, a.followers_count, a.status,
                   a.platform_stats, a.last_post_at, a.contact_email, a.commission_status,
                   a.commission_confidence, a.commission_detail, a.commission_checked_at,
                   aa.confidence, p.slug as platform, p.display_only,
                   (select s.bio_text from account_snapshots s
                    where s.account_id = a.id order by s.captured_at desc limit 1) as bio
            from artist_accounts aa
            join accounts a on a.id = aa.account_id
            join platforms p on p.id = a.platform_id
            where aa.artist_id = %s and aa.removed_at is null
            order by p.display_rank, a.followers_count desc nulls last""", (artist_id,))
        connections = q(conn, """
            select distinct on (oa.id)
                   e.id as edge_id,
                   case when e.source_account_id = m.account_id then 'outgoing' else 'incoming' end as direction,
                   e.relation_hint, e.matched_text, e.evidence_url,
                   oa.id as other_id, oa.handle::text as other_handle,
                   oa.display_name as other_display_name,
                   oa.profile_url as other_profile_url, oa.followers_count as other_followers,
                   op.slug as other_platform
            from identity_edges e
            join (select account_id from artist_accounts
                  where artist_id = %(id)s and removed_at is null) m
              on m.account_id in (e.source_account_id, e.target_account_id)
            join accounts oa on oa.id = case when e.source_account_id = m.account_id
                                             then e.target_account_id else e.source_account_id end
            join platforms op on op.id = oa.platform_id
            where e.claim = 'related' and e.status = 'present'
              -- Skip edges whose other end is already an account of this artist:
              -- that link is internal to a merge, not an external connection.
              and oa.id not in (select account_id from artist_accounts
                                where artist_id = %(id)s and removed_at is null)
            order by oa.id, e.id""", {"id": artist_id})
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
        return render_template(
            "artist.html", artist=artist, accounts=accounts, signals=signals,
            connections=connections, events=events,
            avatar=next((a["avatar_url"] for a in accounts
                         if a.get("avatar_url")), None),
            suppressed=suppressed_rows[0] if suppressed_rows else None,
            badge=any(s["kind"] == "attestation" for s in signals),
            nsfw=any(s["kind"] == "content_flag" for s in signals),
            pending=pending_count(conn), demoted_count=demoted_count(conn))


@app.route("/artist/<int:artist_id>/detach/<int:account_id>", methods=["POST"])
def detach(artist_id, account_id):
    """Remove an account from an artist: membership closes (admin event, so
    clustering never re-attaches it) and connecting edges become related, so
    the account stays visible as a connection."""
    with db.connect() as conn, conn.cursor() as cur:
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
        conn.commit()
    return redirect(url_for("artist", artist_id=artist_id))


@app.route("/artist/<int:artist_id>/confirm/<int:account_id>", methods=["POST"])
def confirm_connection(artist_id, account_id):
    """Inverse of detach: a human vouches that a 'related' connection is in fact
    the same person. If the account belongs to another artist, merge them;
    otherwise attach the floating account. Either way the connecting edges
    become same_person so re-extraction keeps them merged."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("""select artist_id from artist_accounts
                       where account_id = %s and removed_at is null""", (account_id,))
        row = cur.fetchone()
        other_artist = row[0] if row else None
        if other_artist and other_artist != artist_id:
            from .cluster import merge_artists
            merge_artists(conn, artist_id, [other_artist], actor="admin:review-ui")
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
        conn.commit()
    return redirect(url_for("artist", artist_id=artist_id))


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


def _enrich(conn, item):
    payload = item["payload"]
    ctx = {}
    if item["kind"] == "one_directional_attach":
        src = q(conn, """select a.handle::text as handle, ar.public_slug, ar.id as artist_id
                         from accounts a
                         join artist_accounts aa on aa.account_id = a.id and aa.removed_at is null
                         join artists ar on ar.id = aa.artist_id
                         where a.id = %s""", (payload["source_account_id"],))
        # Live values — the payload snapshot goes stale (targets get hydrated
        # after the item was created).
        tgt = q(conn, """select a.handle::text as handle, a.followers_count, p.slug as platform
                         from accounts a join platforms p on p.id = a.platform_id
                         where a.id = %s""", (payload["target_account_id"],))
        edge = q(conn, """select evidence_type, evidence_url, matched_text
                          from identity_edges where id = %s""", (payload["edge_id"],))
        if src and tgt:
            ctx = {"artist_id": src[0]["artist_id"], "artist_slug": src[0]["public_slug"],
                   "source_handle": src[0]["handle"], "target_handle": tgt[0]["handle"],
                   "target_platform": tgt[0]["platform"],
                   "target_followers": tgt[0]["followers_count"],
                   "evidence_type": edge[0]["evidence_type"] if edge else None,
                   "matched_text": edge[0]["matched_text"] if edge else None,
                   "evidence_url": edge[0]["evidence_url"] if edge else None,
                   "reason": payload.get("reason") or payload.get("evidence")}
    elif item["kind"] == "cluster_merge":
        ids = json.loads(payload["artist_ids"])
        artists = q(conn, """
            select ar.id, ar.public_slug,
                   (select max(a.followers_count) from artist_accounts aa
                    join accounts a on a.id = aa.account_id
                    where aa.artist_id = ar.id and aa.removed_at is null) as followers
            from artists ar where ar.id = any(%s) order by ar.id""", (ids,))
        # WHAT connects them: every present edge whose endpoints sit in
        # different artists of this pair.
        evidence = q(conn, """
            select e.evidence_type, e.claim, e.evidence_url, e.matched_text,
                   sa.handle::text as src_handle, sp.slug as src_platform,
                   ta.handle::text as tgt_handle, tp.slug as tgt_platform
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
            limit 8""", {"ids": ids})
        ctx = {"artists": artists, "evidence": evidence,
               "keeper_slug": artists[0]["public_slug"] if artists else "?"}
    elif item["kind"] == "singleton_gate":
        bio = q(conn, """select bio_text from account_snapshots
                         where account_id = %s order by captured_at desc limit 1""",
                (payload["account_id"],))
        ctx = {"bio": bio[0]["bio_text"] if bio else None}
    item["ctx"] = ctx
    return item


@app.route("/review")
def review():
    with db.connect() as conn:
        items = [_enrich(conn, i) for i in
                 q(conn, "select * from review_items where status = 'pending' order by created_at")]
        merge_items = [i for i in items if i["kind"] == "cluster_merge"
                       or (i["kind"] == "other" and i["payload"].get("type") != "anomaly")]
        anomaly_items = [i for i in items
                         if i["kind"] == "other" and i["payload"].get("type") == "anomaly"]
        attach_items = [i for i in items if i["kind"] not in ("cluster_merge", "other")]
        return render_template("review.html", merge_items=merge_items,
                               anomaly_items=anomaly_items,
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
            merge_artists(conn, ids[0], ids[1:], actor="admin:review-ui")


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
    return redirect(url_for("review"))


@app.route("/review/bulk", methods=["POST"])
def bulk_decide():
    decision = request.form.get("decision")
    assert decision in ("approve", "reject")
    with db.connect() as conn:
        for item_id in request.form.getlist("items"):
            _decide_one(conn, int(item_id), decision)
        conn.commit()
    return redirect(url_for("review"))


def main():
    app.run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == "__main__":
    main()
