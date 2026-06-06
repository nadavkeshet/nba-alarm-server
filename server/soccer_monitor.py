"""Soccer alarm radar — polling thread that fires event-driven alarms.

Architecture in P2:
- One global thread (poll every 25s).
- On each tick: query all active soccer_alarms with a match_id (per-match alarms).
- Group alarms by match_id; fetch detail once per match (adapter caches anyway).
- Evaluate each alarm against the current state; fire via shared FCM helper.
- Mark one-shot alarms as fired (sets fired_at, is_active=False).

What we support in P2 (all event-driven, no Drama Engine yet):
- match            — fire when status flips to live (kickoff notify)
- late_drama       — minute >= 75 AND |home_score - away_score| <= 1
- knockout_et      — match enters extra-time or penalty shootout
- penalty          — penalty awarded incident, just-in-time
- red_card         — red card in last 20 min while score within 1

What's NOT in P2:
- Radar-style alarms (no specific match_id) — needs Drama Engine for the
  "fire on any worth-watching match" semantics. P3.
- hat_trick — superseded by player_alert's hat_trick_watch.
- drama_radar — P3 (Drama Engine).

Implemented since P3:
- comeback     — a side that trailed by 2+ has pulled back to within 1.
- big_chance   — a burst of chances (recent-xG flurry) for a side / either side.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import soccer_source as ss
import drama_engine
import bzzoiro_adapter
from i18n import (
    t_title,
    t_soccer_body_kickoff,
    t_soccer_body_reminder,
    t_soccer_body_lineups,
    t_soccer_body_halftime,
    t_soccer_body_goal,
    t_soccer_body_second_half,
    t_soccer_body_match_end,
    t_soccer_body_knockout_et,
    t_soccer_body_late_drama,
    t_soccer_body_penalty,
    t_soccer_body_red_card,
    t_soccer_body_sensation,
    t_soccer_body_comeback,
    t_soccer_body_big_chance,
    t_soccer_body_drama_radar,
    t_soccer_body_player_goal,
    t_soccer_body_player_brace,
    t_soccer_body_player_hattrick,
    t_soccer_body_player_yellow,
    t_soccer_body_player_red,
    t_shist,
)
import soccer_historic

logger = logging.getLogger(__name__)

# Wired by main.py at startup so this module stays import-safe even when
# firebase_admin / SQLAlchemy aren't initialized (e.g. unit tests).
_send_push: Callable[..., None] | None = None
_get_user_language: Callable[[str], str] | None = None
_fire_once_fn: Callable[..., bool] | None = None
_db_engine: Any = None
_alarm_model: Any = None  # SoccerAlarmDB class — injected to avoid circular import
_comp_follow_model: Any = None  # SoccerFavoriteCompetitionDB — followed leagues
_team_follow_model: Any = None  # SoccerFavoriteTeamDB — followed teams
_soccer_historic_model: Any = None  # SoccerHistoricPrefDB — per-user historic config

POLL_INTERVAL = 25.0  # seconds

# Pre-match pass throttle + per-team fixtures cache (module-level so they
# survive across ticks). The pass evaluates favorite teams' upcoming matches
# for "reminder before kickoff" + "confirmed lineups" alerts.
_PREMATCH_INTERVAL = 60.0          # run the pass body at most once per ~60s
_PREMATCH_FIXTURES_TTL = 600.0     # cache a team's fixtures for ~10 min
_PREMATCH_HORIZON = 3 * 3600.0     # only consider matches within the next 3h
_LINEUP_WINDOW = 90 * 60.0         # only poll lineups within 90 min of kickoff
_last_prematch_ts: float = 0.0
# {team_id: (expiry_monotonic, next_match_dict_or_None)}
_prematch_fixtures: dict[str, tuple[float, dict | None]] = {}


def _reset_prematch_for_tests() -> None:
    """Clear the pre-match throttle + per-team fixtures cache so unit tests
    run deterministically (each call exercises a fresh pass)."""
    global _last_prematch_ts
    _last_prematch_ts = 0.0
    _prematch_fixtures.clear()

# Statuses that mean the match is definitively over — deactivate all its alarms immediately.
_TERMINAL_STATUSES = {"ft", "aet", "ap", "cancelled", "postponed", "abandoned"}


def wire(
    *,
    send_push: Callable[..., None],
    get_user_language: Callable[[str], str],
    fire_once: Callable[..., bool],
    db_engine: Any,
    alarm_model: Any,
    comp_follow_model: Any = None,
    team_follow_model: Any = None,
    soccer_historic_model: Any = None,
) -> None:
    """Called once by main.py during startup to inject runtime dependencies."""
    global _send_push, _get_user_language, _fire_once_fn, _db_engine, _alarm_model
    global _comp_follow_model, _team_follow_model, _soccer_historic_model
    _send_push = send_push
    _get_user_language = get_user_language
    _fire_once_fn = fire_once
    _db_engine = db_engine
    _alarm_model = alarm_model
    _comp_follow_model = comp_follow_model
    _team_follow_model = team_follow_model
    _soccer_historic_model = soccer_historic_model


# ---------------------------------------------------------------------------
# Lifecycle


_thread: threading.Thread | None = None
_stop = threading.Event()
# Set whenever the BSD WebSocket pushes a fresh `event` frame for any
# subscribed match. The radar wait loop watches it so an actual score /
# state change wakes us within ~1s instead of waiting up to POLL_INTERVAL.
_wake = threading.Event()


def start_soccer_radar() -> None:
    """Spawn the radar polling thread. Idempotent."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _wake.clear()
    _thread = threading.Thread(target=_radar_loop, name="soccer-radar", daemon=True)
    _thread.start()
    _register_ws_callback()
    logger.info("soccer radar thread started (poll=%.1fs, ws-wake enabled)", POLL_INTERVAL)


def stop_soccer_radar() -> None:
    _stop.set()


def is_radar_running() -> bool:
    return _thread is not None and _thread.is_alive()


def _register_ws_callback() -> None:
    """Wire the BSD WS push stream into the radar so score changes
    short-circuit the poll interval. Best-effort: if the WS module isn't
    importable or the singleton hasn't started, the radar just keeps
    polling on its normal cadence."""
    try:
        import bsd_websocket as bw
        client = bw.client()

        def _on_push(event_id: int, kind: str, payload: dict) -> None:
            # Only react to score/time updates — livedata frames stream
            # every ~5s while a match is live and would otherwise wake the
            # radar constantly with no actionable change.
            if kind == "event":
                _wake.set()

        client.add_callback(_on_push)
        logger.info("soccer radar ↔ BSD WS callback wired")
    except Exception as exc:
        logger.warning("soccer radar: WS callback not wired: %s", exc)


# ---------------------------------------------------------------------------
# Main loop


def _radar_loop() -> None:
    while not _stop.is_set():
        try:
            _tick()
        except Exception as exc:
            logger.exception("soccer radar tick failed: %s", exc)
        # Wait up to POLL_INTERVAL OR until the WS push wakes us. Whichever
        # fires first, the next tick runs immediately after.
        woken = _wake.wait(POLL_INTERVAL)
        if woken:
            _wake.clear()


def _tick() -> None:
    if _alarm_model is None or _db_engine is None:
        logger.debug("soccer radar tick skipped — not wired yet")
        return

    from sqlalchemy.orm import Session  # local import to keep module import-safe
    with Session(_db_engine) as session:
        rows = session.query(_alarm_model).filter(
            _alarm_model.is_active.is_(True),
        ).all()
        if not rows:
            return
        # Snapshot to plain dicts so we can close the session before doing network I/O
        alarms = [
            {
                "id": r.id,
                "user_token": r.user_token,
                "alarm_type": r.alarm_type,
                "match_id": r.match_id,
                "competition_id": r.competition_id,
                "player_id": r.player_id,
                "threshold": r.threshold,
                "alert_type": r.alert_type,
                "config": r.config or {},
            }
            for r in rows
        ]

    # Two passes:
    # 1) Match-scoped alarms (match_id != None) — fetch each match once.
    # 2) Comp-scoped drama_radar alarms (match_id == None, competition_id != None)
    #    — fetch live matches in each requested competition once.

    match_scoped: dict[str, list[dict]] = defaultdict(list)
    comp_radar:   list[dict]            = []
    for a in alarms:
        if a["match_id"]:
            match_scoped[a["match_id"]].append(a)
        elif a["alarm_type"] == "drama_radar" and a["competition_id"]:
            comp_radar.append(a)

    # Low-latency wake: subscribe the BSD-canonical matches we're watching to the
    # BSD WebSocket so their `event` frames flip `_wake` and the next tick runs
    # within ~1s of a real state change (instead of waiting POLL_INTERVAL). 365
    # matches have no WS and rely on the normal poll — that's expected.
    # Best-effort: a WS hiccup must never break the radar tick.
    try:
        bsd_event_ids = {
            int(mid[4:]) for mid in match_scoped
            if mid.startswith("bsd-") and mid[4:].isdigit()
        }
        if bsd_event_ids:
            import bsd_websocket as bw
            client = bw.client()
            already = client.desired_subscriptions()
            for eid in bsd_event_ids - already:
                client.subscribe(eid)  # additive — don't clobber other subscribers
    except Exception as exc:
        logger.debug("soccer radar: WS subscribe sync skipped: %s", exc)

    # ── Pass 1: per-match
    for match_id, alarm_group in match_scoped.items():
        # A bsd-* id must be numeric after the prefix. A malformed one (e.g. a
        # leftover "bsd-test-match-1" fixture) can never resolve, so polling it
        # every cycle just spams warnings forever — deactivate it once.
        if match_id.startswith("bsd-") and not match_id[4:].isdigit():
            _deactivate_alarms([a["id"] for a in alarm_group],
                               reason=f"malformed match id {match_id}")
            continue
        try:
            detail = ss.get_soccer_match_detail(match_id)
        except Exception as exc:
            logger.warning("soccer radar: detail fetch failed for %s: %s", match_id, exc)
            continue
        if detail is None:
            continue

        # If the match is over, deactivate every alarm for it immediately — no more polling.
        # EXCEPT: match_moments alarms want a full-time alert, so evaluate them
        # once on this terminal tick BEFORE deactivating (the mm_ft moment fires
        # here; without this the sweep would skip evaluation and it'd be lost).
        status = (detail.get("status") or "").lower()
        if status in _TERMINAL_STATUSES:
            for alarm in alarm_group:
                if alarm["alarm_type"] == "match_moments":
                    try:
                        _evaluate_alarm(alarm, detail)
                    except Exception as exc:
                        logger.exception("alarm %s ft eval failed: %s", alarm["id"], exc)
            ids = [a["id"] for a in alarm_group]
            _deactivate_alarms(ids, reason=f"match {match_id} status={status}")
            continue

        for alarm in alarm_group:
            try:
                _evaluate_alarm(alarm, detail)
            except Exception as exc:
                logger.exception("alarm %s eval failed: %s", alarm["id"], exc)

    # ── Pass 2: competition-scoped evaluation.
    # Three sources, all fanned out per competition so each comp's live matches
    # are fetched once and its drama score computed once:
    #   • real comp-scoped drama_radar alarm rows (match_id None) from `alarms`
    #   • followed competitions — FULL criteria applied to every match in the comp
    #   • followed teams — FULL criteria applied to the team's matches only
    # Followed team/comp criteria mirror a per-match alarm 1:1 (drama_radar,
    # late_drama, match_moments, card_penalty, sensation); silent / paused follows
    # are display-only ("Just Follow") and never alert.
    drama_by_comp: dict[str, list[dict]] = defaultdict(list)
    for a in comp_radar:
        if a.get("competition_id"):
            drama_by_comp[a["competition_id"]].append(a)

    specs_by_comp: dict[str, list[dict]] = defaultdict(list)
    for spec in _load_follow_specs():
        for slug in spec["competitions"]:
            if slug:
                specs_by_comp[slug].append(spec)

    # Football Historic Moments: also iterate comps that historic-enabled users
    # follow (even via silent / just-follow favorites, which specs_by_comp omits).
    hist_cfgs, hist_follows = _load_historic_state()
    hist_comps: set[str] = set()
    for _u in hist_cfgs:
        hist_comps |= hist_follows.get(_u, {}).get("comps", set())

    for comp_slug in set(drama_by_comp) | set(specs_by_comp) | hist_comps:
        try:
            live_matches = ss.get_live_matches([comp_slug])
        except Exception as exc:
            logger.warning("pass2: live fetch failed for %s: %s", comp_slug, exc)
            continue
        for slim_match in live_matches:
            cid = slim_match.get("id")
            if not cid:
                continue
            try:
                detail = ss.get_soccer_match_detail(cid)
            except Exception as exc:
                logger.warning("pass2: detail fetch failed for %s: %s", cid, exc)
                continue
            if detail is None:
                continue
            # Drama score computed ONCE per match — shared across every consumer.
            match_score = _match_drama(detail)
            home_id = str((detail.get("home") or {}).get("id") or "")
            away_id = str((detail.get("away") or {}).get("id") or "")

            # Real comp-scoped drama_radar alarm rows.
            for alarm in drama_by_comp.get(comp_slug, []):
                try:
                    if match_score is not None:
                        _evaluate_drama_radar(alarm, detail, score=match_score)
                except Exception as exc:
                    logger.exception("drama_radar alarm %s eval failed: %s", alarm["id"], exc)

            # Team / competition follows → full per-match criteria.
            for spec in specs_by_comp.get(comp_slug, []):
                tids = spec.get("team_ids")
                if tids is not None and home_id not in tids and away_id not in tids:
                    continue  # team follow: only the team's own matches
                for alarm in _expand_follow_criteria(spec, cid, detail):
                    try:
                        if alarm["alarm_type"] == "drama_radar":
                            if match_score is not None:
                                _evaluate_drama_radar(alarm, detail, score=match_score)
                        else:
                            _evaluate_alarm(alarm, detail)
                    except Exception as exc:
                        logger.exception("follow %s criterion %s eval failed: %s",
                                         spec["id"], alarm["alarm_type"], exc)

            # Football Historic Moments for everyone following this match.
            if hist_cfgs:
                try:
                    _evaluate_historic(cid, detail, home_id, away_id, hist_cfgs, hist_follows)
                except Exception as exc:
                    logger.exception("historic eval failed for %s: %s", cid, exc)

    # ── Pre-match pass: reminders + confirmed lineups for favorite teams.
    # Self-throttled (~60s); best-effort so a failure never aborts the tick.
    try:
        _prematch_pass()
    except Exception as exc:
        logger.exception("soccer prematch pass failed: %s", exc)


def _load_follow_specs() -> list[dict]:
    """Active team + competition follows as evaluation specs. Each:
    {id, user_token, competitions:[slug], team_ids:set|None, alert_type,
     threshold, notify_at_kickoff, config}. A None team_ids means "every match
     in the competition" (a league follow); a set scopes to the team's matches."""
    specs: list[dict] = []
    if _db_engine is None:
        return specs
    from sqlalchemy.orm import Session

    def _row_spec(*, sid, token, comps, team_ids, alert_type, threshold,
                  notify_at_kickoff, config):
        return {
            "id": sid, "user_token": token, "competitions": comps,
            "team_ids": team_ids, "alert_type": alert_type or "alarm",
            "threshold": threshold,
            "notify_at_kickoff": bool(notify_at_kickoff),
            "config": config or {},
        }

    if _comp_follow_model is not None:
        try:
            with Session(_db_engine) as session:
                rows = session.query(_comp_follow_model).all()
            for f in rows:
                if (f.alert_type or "silent") == "silent" or getattr(f, "paused", False):
                    continue
                specs.append(_row_spec(
                    sid=f"compfollow_{f.user_token}_{f.competition_id}",
                    token=f.user_token, comps=[f.competition_id], team_ids=None,
                    alert_type=f.alert_type, threshold=f.drama_threshold,
                    notify_at_kickoff=getattr(f, "notify_at_kickoff", False),
                    config=getattr(f, "config", None)))
        except Exception as exc:
            logger.warning("comp-follow load failed: %s", exc)

    if _team_follow_model is not None:
        try:
            with Session(_db_engine) as session:
                rows = session.query(_team_follow_model).all()
            for f in rows:
                if (f.alert_type or "silent") == "silent" or getattr(f, "paused", False):
                    continue
                comps = [c for c in (f.competitions or "").split(",") if c]
                if not comps and f.competition_id:
                    comps = [f.competition_id]
                tids = {f.team_id} | {t for t in (f.alt_team_ids or "").split(",") if t}
                specs.append(_row_spec(
                    sid=f"teamfollow_{f.user_token}_{f.team_id}",
                    token=f.user_token, comps=comps, team_ids=tids,
                    alert_type=f.alert_type, threshold=f.drama_threshold,
                    notify_at_kickoff=getattr(f, "notify_at_kickoff", False),
                    config=getattr(f, "config", None)))
        except Exception as exc:
            logger.warning("team-follow load failed: %s", exc)
    return specs


# ── Football Historic Moments ───────────────────────────────────────────────
# Per-user opt-in "historic moment" alerts (hat-trick, rout, last-gasp winner,
# mass red cards, …). Global per user — applies to every match they follow
# (team or league), independent of the per-follow alarm criteria. Detection is
# in soccer_historic.detect_historic; config + dedup are wired here.
_HIST_INTERVAL = 60.0
_hist_cfg_cache: dict[str, dict] = {}
_hist_follows_cache: dict[str, dict] = {}
_hist_cache_ts: float = 0.0


def _load_historic_state() -> tuple[dict, dict]:
    """(configs, follows) for users with historic enabled. `configs[user] =
    {alert_type, categories}`. `follows[user] = {"comps": set, "teams": set}`,
    INCLUDING silent / just-follow favorites (historic applies to any favorite,
    not only alerting ones). Both throttled together (~60s)."""
    global _hist_cfg_cache, _hist_follows_cache, _hist_cache_ts
    now = time.monotonic()
    if now - _hist_cache_ts < _HIST_INTERVAL and _hist_cache_ts > 0:
        return _hist_cfg_cache, _hist_follows_cache
    cfgs: dict[str, dict] = {}
    follows: dict[str, dict] = {}
    if _db_engine is not None and _soccer_historic_model is not None:
        from sqlalchemy.orm import Session
        try:
            with Session(_db_engine) as session:
                for r in session.query(_soccer_historic_model).filter(
                        _soccer_historic_model.enabled.is_(True)).all():
                    cfgs[r.user_token] = {
                        "alert_type": r.alert_type or "silent",
                        "categories": {k for k, v in (r.categories or {}).items() if v},
                    }
        except Exception as exc:
            logger.warning("historic config load failed: %s", exc)
        if cfgs:
            def _bucket(token):
                return follows.setdefault(token, {"comps": set(), "teams": set()})
            try:
                with Session(_db_engine) as session:
                    if _comp_follow_model is not None:
                        for f in session.query(_comp_follow_model).all():
                            if f.user_token in cfgs and f.competition_id:
                                _bucket(f.user_token)["comps"].add(f.competition_id)
                    if _team_follow_model is not None:
                        for f in session.query(_team_follow_model).all():
                            if f.user_token not in cfgs:
                                continue
                            b = _bucket(f.user_token)
                            b["comps"].update(c for c in (f.competitions or "").split(",") if c)
                            if f.competition_id:
                                b["comps"].add(f.competition_id)
                            b["teams"].add(str(f.team_id))
                            b["teams"].update(t for t in (f.alt_team_ids or "").split(",") if t)
            except Exception as exc:
                logger.warning("historic follow load failed: %s", exc)
    _hist_cfg_cache, _hist_follows_cache, _hist_cache_ts = cfgs, follows, now
    return cfgs, follows


def _reset_historic_for_tests() -> None:
    global _hist_cache_ts
    _hist_cache_ts = 0.0


def _historic_users_for_match(comp_slug: str, home_id: str, away_id: str,
                              cfgs: dict, follows: dict) -> list[str]:
    """Users (with historic enabled) who follow this match — via a league follow
    on its competition or a team follow on either side."""
    out = []
    for user in cfgs:
        f = follows.get(user)
        if not f:
            continue
        if comp_slug in f["comps"] or home_id in f["teams"] or away_id in f["teams"]:
            out.append(user)
    return out


def _evaluate_historic(cid: str, detail: dict, home_id: str, away_id: str,
                       cfgs: dict, follows: dict) -> None:
    """Detect historic moments once per match, fire each enabled category for
    every following user. Dedup is per (user, match, moment) via a stable
    `shist_{cid}` alarm id so two follows of the same match don't double-fire."""
    users = _historic_users_for_match(comp_slug=detail.get("competition") or "",
                                      home_id=home_id, away_id=away_id,
                                      cfgs=cfgs, follows=follows)
    if not users:
        return
    try:
        moments = soccer_historic.detect_historic(detail)  # positions unavailable in radar loop
    except Exception as exc:
        logger.warning("historic detect failed for %s: %s", cid, exc)
        return
    if not moments:
        return
    home_name = (detail.get("home") or {}).get("name", "")
    away_name = (detail.get("away") or {}).get("name", "")
    competition = detail.get("competition", "")
    for user in users:
        cfg = cfgs[user]
        enabled_cats = cfg["categories"]
        alert_type = cfg["alert_type"]
        for m in moments:
            cat = m.get("category")
            if cat not in enabled_cats:
                continue
            params = dict(m.get("params") or {})
            params.setdefault("home", home_name)
            params.setdefault("away", away_name)
            params.setdefault("competition", competition)
            dedup_key = f"soccer_shist_{cid}_{cat}_{m.get('dedup', cat)}"
            _fire_historic(user, dedup_key, cat, params, alert_type)


def _fire_historic(user_token: str, dedup_key: str, category: str,
                   params: dict, alert_type: str) -> None:
    if _send_push is None or _fire_once_fn is None:
        return
    if not _fire_once_fn(dedup_key, user_token):
        return
    lang = "en"
    if _get_user_language is not None:
        try:
            lang = _get_user_language(user_token) or "en"
        except Exception:
            pass
    try:
        title, body = t_shist(category, lang, params)
    except Exception as exc:
        logger.warning("historic i18n failed for %s: %s", category, exc)
        return
    try:
        _send_push(user_token, title, body, alert_type=alert_type)
    except Exception as exc:
        logger.warning("historic FCM send failed (%s): %s", category, exc)


def _follow_side(spec: dict, detail: dict) -> str:
    """Which side a team-scoped criterion (comeback / big_chance) should watch:
    'home'/'away' for a team follow on this match, 'both' for a league follow
    (team_ids None) or when the followed team can't be matched to a side."""
    tids = spec.get("team_ids")
    if not tids:
        return "both"
    home_id = str((detail.get("home") or {}).get("id") or "")
    away_id = str((detail.get("away") or {}).get("id") or "")
    if home_id in tids:
        return "home"
    if away_id in tids:
        return "away"
    return "both"


def _expand_follow_criteria(spec: dict, cid: str, detail: dict) -> list[dict]:
    """Turn a follow spec into synthesized per-match alarm dicts the standard
    evaluators understand. Each carries a per-match id so _fire's dedup
    (soccer_{id}_{signal}) is scoped per (follow, match, criterion). When the
    follow has no `config`, fall back to the legacy drama + kickoff behavior."""
    base_id = f"{spec['id']}_{cid}"
    cfg = spec.get("config") or {}
    out: list[dict] = []

    def mk(alarm_type: str, config: dict | None = None, threshold=None) -> dict:
        return {
            "id": base_id, "user_token": spec["user_token"],
            "alarm_type": alarm_type, "match_id": cid, "competition_id": None,
            "player_id": None, "threshold": threshold,
            "alert_type": spec.get("alert_type") or "alarm", "config": config or {},
        }

    if cfg:
        dr = cfg.get("drama_radar") or {}
        if dr.get("enabled"):
            out.append(mk("drama_radar", threshold=dr.get("threshold", 7.0)))
        ld = cfg.get("late_drama") or {}
        if ld.get("enabled"):
            out.append(mk("late_drama", {"max_diff": ld.get("max_diff", 1),
                                         "last_minutes": ld.get("last_minutes", 15)}))
        mm = cfg.get("match_moments") or {}
        if mm.get("enabled"):
            # Modular: pass every per-event flag through (each absent flag
            # defaults ON in the evaluator). `reminder_minutes` + `lineups` are
            # read by the pre-match pass; the live evaluator gates each moment on
            # its own flag.
            def _f(k: str) -> bool:
                return bool(mm.get(k, True))
            out.append(mk("match_moments", {
                "enabled": True,
                "kickoff": _f("kickoff"), "goals": _f("goals"),
                "halftime": _f("halftime"), "second_half": _f("second_half"),
                "red_cards": _f("red_cards"), "penalties": _f("penalties"),
                "match_end": _f("match_end"), "lineups": _f("lineups"),
                "reminder_minutes": int(mm.get("reminder_minutes") or 0)}))
        cp = cfg.get("card_penalty") or {}
        if cp.get("enabled"):
            out.append(mk("card_penalty", {"red_card": bool(cp.get("red_card")),
                                           "penalty": bool(cp.get("penalty"))}))
        sn = cfg.get("sensation") or {}
        if sn.get("enabled"):
            try:
                dog = ss.resolve_underdog(cid, detail=detail)
            except Exception:
                dog = None
            if dog:
                out.append(mk("sensation", {"underdog_is_home": dog["underdog_is_home"],
                                            "underdog_name": dog["underdog_name"]}))
        cb = cfg.get("comeback") or {}
        if cb.get("enabled"):
            out.append(mk("comeback", {"side": _follow_side(spec, detail)}))
        bc = cfg.get("big_chance") or {}
        if bc.get("enabled"):
            out.append(mk("big_chance", {"side": _follow_side(spec, detail),
                                         "sensitivity": bc.get("sensitivity", "medium")}))
    else:
        # Legacy follow (pre-config): drama radar + kickoff only.
        thr = spec.get("threshold")
        if thr is not None and thr < 90:
            out.append(mk("drama_radar", threshold=thr))
        if spec.get("notify_at_kickoff"):
            out.append(mk("match_moments", {"enabled": True, "reminder_minutes": 0}))
    return out


# ---------------------------------------------------------------------------
# Pre-match pass — reminders + confirmed lineups for favorite TEAMS.


def _parse_kickoff(s: str | None):
    """Parse an ISO8601 UTC kickoff ('...Z' or '+00:00') → aware datetime, or None."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _next_upcoming_match(team_id: str, league_slug: str | None, now) -> dict | None:
    """The team's soonest scheduled match within the next horizon, cached per
    team for ~10 min so get_team_season isn't hammered every tick."""
    monot = time.monotonic()
    cached = _prematch_fixtures.get(team_id)
    if cached is not None and cached[0] > monot:
        match = cached[1]
    else:
        try:
            fixtures = ss.get_team_season(team_id, league_slug=league_slug) or []
        except Exception as exc:
            logger.warning("prematch: fixtures fetch failed for team %s: %s", team_id, exc)
            fixtures = []
        match = _pick_imminent(fixtures, now)
        _prematch_fixtures[team_id] = (monot + _PREMATCH_FIXTURES_TTL, match)
    # Re-validate the cached match against the *current* clock — the horizon
    # check above used the now from when it was cached, which is close enough,
    # but a match can leave the window between cache writes; the per-signal
    # window checks below are the real gates, so just return it.
    return match


def _pick_imminent(fixtures: list[dict], now) -> dict | None:
    """From a team's fixtures, the soonest match that is still scheduled and
    kicks off within (0, _PREMATCH_HORIZON]. Returns the match dict or None."""
    best = None
    best_ko = None
    for m in fixtures:
        status = (m.get("status") or "").lower()
        ko = _parse_kickoff(m.get("kickoff"))
        if ko is None:
            continue
        delta = (ko - now).total_seconds()
        if delta <= 0 or delta > _PREMATCH_HORIZON:
            continue
        # Only future/scheduled matches (a live/finished one in-window is noise).
        if status and status != "scheduled":
            continue
        if best_ko is None or ko < best_ko:
            best, best_ko = m, ko
    return best


def _fetch_lineup_status(cid: str) -> str | None:
    """lineup_status for a canonical match id, dispatching by id prefix:
    bsd-* → bzzoiro_adapter.event_lineups; s365-* → soccer_source.get_365_lineups.
    Best-effort: any failure returns None."""
    try:
        source, native = ss.parse_canonical_id(cid)
        eid = int(native)
    except (ValueError, TypeError):
        return None
    try:
        if source == "bsd":
            data = bzzoiro_adapter.event_lineups(eid)
        else:
            data = ss.get_365_lineups(eid)
    except Exception as exc:
        logger.warning("prematch: lineup fetch failed for %s: %s", cid, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data.get("lineup_status")


def _prematch_pass() -> None:
    """For each favorite TEAM follow with match_moments enabled, fire a
    'starting soon' reminder and a 'confirmed lineups' alert for its next
    imminent match. Throttled to ~once/60s; team fixtures cached ~10 min.
    Team follows only — league follows (team_ids None) are skipped."""
    global _last_prematch_ts
    monot = time.monotonic()
    if monot - _last_prematch_ts < _PREMATCH_INTERVAL:
        return
    _last_prematch_ts = monot

    now = datetime.now(timezone.utc)
    for spec in _load_follow_specs():
        try:
            tids = spec.get("team_ids")
            if not tids:
                continue  # league follows have no team scope — reminders are TEAMS only
            cfg = ((spec.get("config") or {}).get("match_moments")) or {}
            if not cfg.get("enabled", True):
                continue

            league_slug = (spec.get("competitions") or [None])[0]
            # Each follow tracks one (or a few alt) team id(s); evaluate each.
            for team_id in tids:
                try:
                    _prematch_team(spec, str(team_id), league_slug, cfg, now)
                except Exception as exc:
                    logger.warning("prematch: team %s failed: %s", team_id, exc)
        except Exception as exc:
            logger.warning("prematch: spec %s failed: %s", spec.get("id"), exc)


def _prematch_team(spec: dict, team_id: str, league_slug: str | None,
                   cfg: dict, now) -> None:
    match = _next_upcoming_match(team_id, league_slug, now)
    if not match:
        return
    cid = match.get("id")
    ko = _parse_kickoff(match.get("kickoff"))
    if not cid or ko is None:
        return
    secs_to_ko = (ko - now).total_seconds()
    if secs_to_ko <= 0:
        return

    home = (match.get("home") or {}).get("name") or ""
    away = (match.get("away") or {}).get("name") or ""
    comp = match.get("competition") or ""

    base_id = f"{spec['id']}_{cid}"
    alarm = {
        "id": base_id,
        "user_token": spec["user_token"],
        "alarm_type": "match_moments",
        "alert_type": spec.get("alert_type") or "alarm",
        "config": cfg,
    }

    # ── Reminder: N min before kickoff.
    reminder_minutes = int(cfg.get("reminder_minutes", 30))
    if reminder_minutes > 0 and 0 < secs_to_ko <= reminder_minutes * 60:
        _fire(
            alarm, "soccer_reminder",
            lambda h, a, c, lang: t_soccer_body_reminder(h, a, c, reminder_minutes, lang),
            home, away, comp, signal="mm_reminder", one_shot=False,
        )

    # ── Confirmed lineups: gated on its flag; only poll inside the 90-min
    # window to bound cost. (Flag absent → ON, for legacy configs.)
    if bool(cfg.get("lineups", True)) and 0 < secs_to_ko <= _LINEUP_WINDOW:
        if _fetch_lineup_status(cid) == "confirmed":
            _fire(
                alarm, "soccer_lineups", t_soccer_body_lineups,
                home, away, comp, signal="mm_lineups", one_shot=False,
            )


# ---------------------------------------------------------------------------
# Evaluators

# comeback: trailed by >= _COMEBACK_DEFICIT, now within _COMEBACK_TARGET (or better)
_COMEBACK_DEFICIT = 2
_COMEBACK_TARGET = 1
# big_chance: expected goals in the last ~15' that count as a "burst", per
# sensitivity. Higher sensitivity = lower bar = fires more readily.
_BIG_CHANCE_XG = {"low": 1.3, "medium": 1.0, "high": 0.7}


# match_id -> ((home_score, away_score), scoring_side) — used to attribute a goal
# to the side whose tally just increased. Idempotent within a tick: repeated
# calls for the same scoreline return the cached attribution (so multiple alarms
# on one match agree and don't consume the transition).
_goal_state: dict[str, tuple[tuple[int, int], str | None]] = {}


def _scoring_side(match_id: str, home_score: int, away_score: int) -> str | None:
    """Which side scored to reach this scoreline ("home"/"away"), or None when it
    can't be attributed (first time we see the match mid-scoreline)."""
    cur = (home_score, away_score)
    prev = _goal_state.get(match_id)
    if prev is not None and prev[0] == cur:
        return prev[1]                       # same scoreline → cached attribution
    side: str | None = None
    if prev is not None:
        ph, pa = prev[0]
        if home_score > ph:
            side = "home"
        elif away_score > pa:
            side = "away"
    _goal_state[match_id] = (cur, side)
    return side


def _goal_scorer(match: dict, home_score: int, away_score: int) -> str | None:
    """Scorer of the goal that produced this exact scoreline, read from the
    detail's `incidents` (already fetched — no extra call, no delay). None when
    the incident hasn't landed yet (the REST incidents lag the WS-fast score),
    so the alert simply omits the scorer rather than waiting for it."""
    for inc in match.get("incidents") or []:
        if not isinstance(inc, dict):
            continue
        if ("goal" in (inc.get("type") or "").lower()
                and inc.get("home_score") == home_score
                and inc.get("away_score") == away_score):
            name = inc.get("player") or inc.get("player_name")
            if name:
                return str(name)
    return None


def _evaluate_alarm(alarm: dict, match: dict) -> None:
    alarm_type = alarm["alarm_type"]
    status = match.get("status")
    home_name = (match.get("home") or {}).get("name", "")
    away_name = (match.get("away") or {}).get("name", "")
    competition = match.get("competition", "")

    if alarm_type == "match":
        # Kickoff notify — fire the first tick after status transitions from scheduled to live.
        if status in ("live", "1h"):
            _fire(alarm, "soccer_kickoff",
                  t_soccer_body_kickoff, home_name, away_name, competition,
                  signal="kickoff", one_shot=True)
        return

    if alarm_type == "late_drama":
        minute = match.get("minute") or 0
        home_score = (match.get("home") or {}).get("score") or 0
        away_score = (match.get("away") or {}).get("score") or 0
        # Configurable: max_diff (0–3, default 1), last_minutes (1–45, default 15).
        # Empty config reproduces the legacy ≥75'/≤1 behavior. Explicit None
        # checks so a chosen max_diff of 0 (tied only) isn't coerced to 1.
        cfg = alarm.get("config") or {}
        md = cfg.get("max_diff")
        max_diff = 1 if md is None else max(0, min(3, int(md)))
        lm = cfg.get("last_minutes")
        last_minutes = 15 if lm is None else max(1, min(45, int(lm)))
        min_minute = max(0, 90 - last_minutes)
        if minute >= min_minute and abs(home_score - away_score) <= max_diff and status in ("live", "1h", "2h"):
            _fire(alarm, "soccer_late_drama",
                  lambda h, a, c, lang: t_soccer_body_late_drama(h, a, c, minute, lang),
                  home_name, away_name, competition,
                  signal="late_drama", one_shot=True)
        return

    if alarm_type == "match_moments":
        # Modular event set — each moment is gated on its OWN config flag
        # (kickoff / goals / halftime / second_half / red_cards / penalties /
        # match_end). A flag absent from config defaults to ON, so legacy configs
        # that only carry `enabled` still fire every moment. Each moment carries a
        # STATE-ENCODED dedup signal so it fires exactly once regardless of which
        # tick catches it; one_shot=False keeps the row alive for later moments.
        # Works identically for BSD- and 365-sourced matches.
        cfg = alarm.get("config") or {}
        if not cfg.get("enabled", True):
            return

        # Pre-kickoff reminder + confirmed-lineups alert. These were previously
        # wired ONLY into the favorite-team follow pre-match pass (_prematch_team),
        # so a PER-MATCH match_moments alarm (set on a specific game from the alarm
        # sheet) never fired its "starts in N min" reminder or its lineups alert.
        # Evaluate them here too. Only meaningful before kickoff; a missing/
        # unparseable kickoff just skips.
        ko = _parse_kickoff(match.get("kickoff"))
        if ko is not None:
            secs_to_ko = (ko - datetime.now(timezone.utc)).total_seconds()
            reminder_minutes = int(cfg.get("reminder_minutes") or 0)
            if reminder_minutes > 0 and 0 < secs_to_ko <= reminder_minutes * 60:
                _fire(alarm, "soccer_reminder",
                      lambda h, a, c, lang: t_soccer_body_reminder(h, a, c, reminder_minutes, lang),
                      home_name, away_name, competition,
                      signal="mm_reminder", one_shot=False)
            if bool(cfg.get("lineups", True)) and 0 < secs_to_ko <= _LINEUP_WINDOW:
                cid = match.get("id")
                if cid and _fetch_lineup_status(cid) == "confirmed":
                    _fire(alarm, "soccer_lineups", t_soccer_body_lineups,
                          home_name, away_name, competition,
                          signal="mm_lineups", one_shot=False)

        def want(flag: str) -> bool:
            return bool(cfg.get(flag, True))
        minute = match.get("minute") or 0
        period = match.get("period")
        home_score = (match.get("home") or {}).get("score") or 0
        away_score = (match.get("away") or {}).get("score") or 0
        # Record the scoreline every tick (even 0-0) so the NEXT change is
        # attributable — otherwise the first goal would have no 0-0 baseline.
        scored = _scoring_side(match.get("id") or "", home_score, away_score)

        # Kickoff — first live tick (status live with period 1h, or period 1h).
        if want("kickoff") and (status in ("live", "1h") or period == "1h"):
            _fire(alarm, "soccer_kickoff", t_soccer_body_kickoff,
                  home_name, away_name, competition, signal="mm_kickoff", one_shot=False)
        # Goals — dedup per scoreline so each distinct goal fires exactly once.
        if want("goals") and (home_score + away_score) > 0 and status not in ("scheduled", None):
            scorer = _goal_scorer(match, home_score, away_score)   # best-effort, no wait
            _fire(alarm, "soccer_goal",
                  lambda h, a, c, lang: t_soccer_body_goal(h, a, home_score, away_score, scored, lang, scorer),
                  home_name, away_name, competition,
                  signal=f"mm_goal_{home_score}-{away_score}", one_shot=False)
        # Half-time start.
        if want("halftime") and (status == "ht" or period == "ht"):
            _fire(alarm, "soccer_halftime",
                  lambda h, a, c, lang: t_soccer_body_halftime(h, a, home_score, away_score, lang),
                  home_name, away_name, competition, signal="mm_ht", one_shot=False)
        # Second-half kickoff (HT end). NB: a bare `minute >= 46` also matches
        # FIRST-HALF stoppage time — 45+1 reads minute 46 while period is still
        # "1h" — which wrongly fired "second half underway" before halftime even
        # arrived. So the minute fallback must exclude the first-half and
        # halftime periods; the reliable signal is period == "2h".
        if want("second_half") and (
            period == "2h"
            or (status == "live" and minute >= 46 and period not in ("1h", "ht"))
        ):
            _fire(alarm, "soccer_second_half", t_soccer_body_second_half,
                  home_name, away_name, competition, signal="mm_2h", one_shot=False)
        # Red card — dedup by the incident minute (mirrors card_penalty).
        if want("red_cards"):
            red = _find_recent_red_card(match)
            if red:
                rmin = red.get("minute") or 0
                _fire(alarm, "soccer_red_card",
                      lambda h, a, c, lang: t_soccer_body_red_card(h, a, c, rmin, lang),
                      home_name, away_name, competition, signal=f"mm_red_{rmin}", one_shot=False)
        # Penalty awarded — dedup by the penalty INCIDENT's minute (stable),
        # NOT the live clock which advances every tick.
        if want("penalties"):
            pen_min = _recent_penalty_minute(match)
            if pen_min is not None:
                _fire(alarm, "soccer_penalty", t_soccer_body_penalty,
                      home_name, away_name, competition, signal=f"mm_pen_{pen_min}", one_shot=False)
        # Match end — MUST fire before the terminal-status sweep deactivates the
        # row. _tick special-cases match_moments alarms so this still runs on the
        # ft tick (see _tick's terminal handling).
        if want("match_end") and status in ("ft", "aet", "ap"):
            _fire(alarm, "soccer_match_end",
                  lambda h, a, c, lang: t_soccer_body_match_end(h, a, c, home_score, away_score, lang),
                  home_name, away_name, competition, signal="mm_ft", one_shot=False)
        return

    if alarm_type == "card_penalty":
        # Modular red-card / penalty. Fire on ANY such event (no close-game gate);
        # per-event dedup signal means each distinct event notifies exactly once.
        cfg = alarm.get("config") or {}
        if cfg.get("red_card"):
            red = _find_recent_red_card(match)
            if red:
                _fire(alarm, "soccer_red_card",
                      lambda h, a, c, lang: t_soccer_body_red_card(h, a, c, red["minute"], lang),
                      home_name, away_name, competition,
                      signal=f"cp_red_{red.get('minute', 0)}", one_shot=False)
        if cfg.get("penalty") and _recent_incident(match, types=("penalty",), window_min=2):
            minute = match.get("minute") or 0
            _fire(alarm, "soccer_penalty",
                  t_soccer_body_penalty, home_name, away_name, competition,
                  signal=f"cp_pen_{minute}", one_shot=False)
        return

    if alarm_type == "sensation":
        # Underdog leading. The underdog side is resolved + stored in config at
        # creation; here we just check whether that side is ahead.
        cfg = alarm.get("config") or {}
        underdog_is_home = bool(cfg.get("underdog_is_home"))
        underdog_name = cfg.get("underdog_name") or (home_name if underdog_is_home else away_name)
        if status in ("live", "1h", "2h", "et"):
            hs = (match.get("home") or {}).get("score") or 0
            as_ = (match.get("away") or {}).get("score") or 0
            leading = (hs > as_) if underdog_is_home else (as_ > hs)
            if leading:
                minute = match.get("minute") or 0
                _fire(alarm, "soccer_sensation",
                      lambda h, a, c, lang: t_soccer_body_sensation(underdog_name, c, minute, lang),
                      home_name, away_name, competition,
                      signal="sensation", one_shot=True)
        return

    if alarm_type == "knockout_et":
        if status in ("et", "pens"):
            _fire(alarm, "soccer_knockout_et",
                  t_soccer_body_knockout_et, home_name, away_name, competition,
                  signal="knockout_et", one_shot=True)
        return

    if alarm_type == "penalty":
        # Look for a penalty incident in last 2 minutes
        if _recent_incident(match, types=("penalty",), window_min=2):
            _fire(alarm, "soccer_penalty",
                  t_soccer_body_penalty, home_name, away_name, competition,
                  signal="penalty", one_shot=False)  # can fire on multiple pens in same match
        return

    if alarm_type == "drama_radar":
        # Match-scoped drama_radar: same evaluator as comp-scoped pass 2.
        _evaluate_drama_radar(alarm, match)
        return

    if alarm_type == "red_card":
        # Red card in last 20 min, score within 1
        home_score = (match.get("home") or {}).get("score") or 0
        away_score = (match.get("away") or {}).get("score") or 0
        if abs(home_score - away_score) > 1:
            return
        minute = match.get("minute") or 0
        red = _find_recent_red_card(match)
        if red and red.get("minute", 0) >= minute - 20:
            _fire(alarm, "soccer_red_card",
                  lambda h, a, c, lang: t_soccer_body_red_card(h, a, c, red["minute"], lang),
                  home_name, away_name, competition,
                  signal=f"red_card_{red.get('minute',0)}", one_shot=True)
        return

    if alarm_type == "comeback":
        # A side that trailed by 2+ goals has clawed back to within one (or
        # drawn level / gone ahead). Reconstructed statelessly from the goal
        # incidents' running scores. `side` scopes to a team (home/away) for a
        # team follow, or "both" for league follows / manual match alarms.
        if status not in ("live", "1h", "2h", "et"):
            return
        side = (alarm.get("config") or {}).get("side", "both")
        sides = _comeback_sides(match)
        if side in ("home", "away"):
            sides &= {side}
        if sides:
            _fire(alarm, "soccer_comeback", t_soccer_body_comeback,
                  home_name, away_name, competition,
                  signal="comeback", one_shot=True)
        return

    if alarm_type == "big_chance":
        # Burst of chances: a side's expected goals over the last ~15' crosses
        # the chosen sensitivity. xg_last_15 is derived from the xg-per-minute
        # timeline; leagues without xG data simply never trip this (no false +).
        # Dedup per 15-minute block so a sustained flurry doesn't spam, but a
        # genuine second flurry later in the match still alerts.
        if status not in ("live", "1h", "2h", "et"):
            return
        cfg = alarm.get("config") or {}
        side = cfg.get("side", "both")
        thr = _BIG_CHANCE_XG.get((cfg.get("sensitivity") or "medium").lower(), 1.0)
        h_xg, a_xg = _recent_xg15(match)
        hit = (h_xg >= thr) if side == "home" else \
              (a_xg >= thr) if side == "away" else \
              (h_xg >= thr or a_xg >= thr)
        if hit:
            block = (match.get("minute") or 0) // 15
            _fire(alarm, "soccer_big_chance", t_soccer_body_big_chance,
                  home_name, away_name, competition,
                  signal=f"bcb_{side}_{block}", one_shot=False)
        return

    # Unknown alarm_type → no-op (keeps forward compat for P3 types)


# ---------------------------------------------------------------------------
# Player alert evaluator


def _is_clutch_goal(is_home: bool | None, home_score: int, away_score: int, minute: int) -> bool:
    """True if this goal is match-decisive: equalizer, go-ahead in 70+, or reduces a 2+ deficit."""
    if is_home is None:
        return False
    scorer_score  = home_score if is_home else away_score
    opponent_score = away_score if is_home else home_score
    diff_after  = scorer_score - opponent_score
    diff_before = (scorer_score - 1) - opponent_score
    if diff_after == 0:                               # equalizer
        return True
    if minute >= 70 and diff_after == 1 and diff_before <= 0:  # late go-ahead
        return True
    if diff_before <= -2 and diff_after > diff_before:         # reduces 2+ deficit
        return True
    return False


def _goal_context_str(is_home: bool | None, home_score: int, away_score: int) -> str:
    if is_home is None:
        return ""
    scorer_score  = home_score if is_home else away_score
    opponent_score = away_score if is_home else home_score
    diff = scorer_score - opponent_score
    if diff == 0:
        return "equalizer"
    if diff == 1:
        return "go-ahead"
    if diff == -1:
        return "comeback"
    return ""


def _evaluate_player_alert(alarm: dict, match: dict) -> None:
    """Evaluate a player_alert alarm against current match incidents.

    Config keys (all optional, sensible defaults):
        goal          bool  Fire on every goal.              default True
        clutch_only   bool  Only fire on clutch goals.       default False
        hat_trick_watch bool  Brace + hat-trick escalation.  default True
        card_yellow   bool  Yellow card alert.               default False
        card_red      bool  Red card alert.                  default True
    """
    player_id = str(alarm.get("player_id") or "")
    if not player_id:
        return

    config = alarm.get("config") or {}
    goal_enabled    = bool(config.get("goal", True))
    clutch_only     = bool(config.get("clutch_only", False))
    hat_trick_watch = bool(config.get("hat_trick_watch", True))
    card_yellow     = bool(config.get("card_yellow", False))
    card_red        = bool(config.get("card_red", True))

    incidents  = match.get("incidents") or []
    home_name  = (match.get("home") or {}).get("name", "")
    away_name  = (match.get("away") or {}).get("name", "")
    competition = match.get("competition", "")

    # Gather all goals by this player (chronological — incidents are ordered)
    player_goals = [
        inc for inc in incidents
        if (inc.get("type") or "").lower() == "goal"
        and str(inc.get("player_id") or "") == player_id
    ]
    p_name = player_goals[0].get("player", "") if player_goals else ""

    # ── Hat-trick watch ──────────────────────────────────────────────────────
    if hat_trick_watch and player_goals:
        count = len(player_goals)
        if count == 2:
            _fire(alarm, "soccer_player_brace",
                  lambda h, a, c, lang, _n=p_name: t_soccer_body_player_brace(_n, c, lang),
                  home_name, away_name, competition,
                  signal="brace", one_shot=False)
        elif count >= 3:
            _fire(alarm, "soccer_player_hattrick",
                  lambda h, a, c, lang, _n=p_name: t_soccer_body_player_hattrick(_n, c, lang),
                  home_name, away_name, competition,
                  signal="hattrick", one_shot=False)

    # ── Individual goal alerts ───────────────────────────────────────────────
    if goal_enabled:
        for inc in player_goals:
            minute    = inc.get("minute") or 0
            hs        = inc.get("home_score") or 0
            as_       = inc.get("away_score") or 0
            is_home   = inc.get("is_home")
            is_clutch = _is_clutch_goal(is_home, hs, as_, minute)
            if clutch_only and not is_clutch:
                continue
            ctx      = _goal_context_str(is_home, hs, as_)
            inc_name = inc.get("player") or p_name
            title_key = "soccer_player_clutch_goal" if (clutch_only and is_clutch) else "soccer_player_goal"
            _fire(
                alarm, title_key,
                lambda h, a, c, lang, _n=inc_name, _hs=hs, _as=as_, _m=minute, _ctx=ctx:
                    t_soccer_body_player_goal(_n, _hs, _as, _m, c, _ctx, lang),
                home_name, away_name, competition,
                signal=f"goal_m{minute}", one_shot=False,
            )

    # ── Card watch ───────────────────────────────────────────────────────────
    if card_yellow or card_red:
        for inc in incidents:
            if (inc.get("type") or "").lower() != "card":
                continue
            if str(inc.get("player_id") or "") != player_id:
                continue
            card_type  = (inc.get("card_type") or "").lower()
            is_yellow  = "yellow" in card_type and "red" not in card_type
            is_red     = "red" in card_type
            minute     = inc.get("minute") or 0
            card_name  = inc.get("player") or p_name
            if card_yellow and is_yellow:
                _fire(
                    alarm, "soccer_player_yellow",
                    lambda h, a, c, lang, _n=card_name, _m=minute:
                        t_soccer_body_player_yellow(_n, h, a, _m, lang),
                    home_name, away_name, competition,
                    signal=f"yellow_m{minute}", one_shot=False,
                )
            if card_red and is_red:
                _fire(
                    alarm, "soccer_player_red",
                    lambda h, a, c, lang, _n=card_name, _m=minute:
                        t_soccer_body_player_red(_n, h, a, _m, lang),
                    home_name, away_name, competition,
                    signal=f"red_m{minute}", one_shot=False,
                )


# ---------------------------------------------------------------------------
# Drama Engine evaluator (P3)


_DEFAULT_DRAMA_THRESHOLD = 7.0


def _match_drama(match: dict):
    """Compute the Drama Engine score for a match ONCE per tick (and log one
    snapshot). Returns the score, or None when the match isn't in a live-like
    phase. The competition-radar pass computes this once per match and shares it
    across every follower, instead of recomputing — and re-writing the DB
    snapshot — once per subscriber (which doesn't scale)."""
    status = (match.get("status") or "").lower()
    # Only meaningful during a live or live-like phase
    if status not in {"live", "ht", "et", "pens", "1h", "2h"}:
        return None
    ctx = drama_engine.context_from_match_detail(match)
    # Enrich with LIVE WebSocket signals the REST detail doesn't carry, so
    # drama_radar ALARMS score off the same real-time data the watch badges do:
    #   • win_prob_swing — a favorite-flip (goal / red card) spikes _score_odds
    #     (up to +0.8). context_from_match_detail leaves it None "by design, the
    #     caller injects it" — and the caller never did, until now.
    #   • big_chances / big_chances_missed / hit_woodwork / big_saves — feed the
    #     enriched _score_chances "thrill" term (near-miss agony + keeper heroics).
    #   • dangerous-attack pressure → momentum_pressure, a faster-updating
    #     momentum signal than the per-minute xG array.
    # Best-effort; only bsd-* ids have a WS feed, so a miss just leaves defaults.
    mid = match.get("id") or ""
    if mid.startswith("bsd-") and mid[4:].isdigit():
        try:
            import bsd_websocket as _ws
            eid = int(mid[4:])
            swing = _ws.get_winprob_swing(eid)
            if swing is not None:
                ctx["win_prob_swing"] = swing
            sig = drama_engine.live_signals_from_ws_event(_ws.get_live_event_state(eid))
            for side in ("home", "away"):
                side_sig = sig.get(side) or {}
                dst = ctx.setdefault(side, {})
                for k, v in side_sig.items():
                    if v is not None:
                        dst[k] = v
            # Momentum: blend the cumulative dangerous-attack asymmetry with the
            # live situation-stream surge (a late flurry of shots/dangerous
            # attacks). Both beat the REST momentum array for responsiveness.
            blended = drama_engine.blend_pressure(
                sig.get("momentum_pressure"), _ws.get_attack_surge(eid))
            if blended is not None:
                ctx["momentum_pressure"] = blended
        except Exception:
            pass
    score = drama_engine.compute_drama(ctx)
    # Persist snapshot (best-effort — never block firing on log failure)
    _log_drama_snapshot(match.get("id"), match.get("minute") or 0, score)
    return score


def _evaluate_drama_radar(alarm: dict, match: dict, *, score=None) -> None:
    """Fire if the match's Drama Engine score crosses the user's threshold.
    `score` may be precomputed (the comp-radar pass shares one score across all
    followers); when omitted it's computed for this single alarm."""
    if score is None:
        score = _match_drama(match)
    if score is None:
        return

    threshold = alarm.get("threshold") if alarm.get("threshold") is not None else _DEFAULT_DRAMA_THRESHOLD
    if score.total < threshold:
        return

    # Bucket the score so a hovering 7.2 doesn't re-fire each tick. A bump
    # from 7.x → 8.x will, because the bucket changes.
    bucket = int(score.total)
    home_name = (match.get("home") or {}).get("name", "")
    away_name = (match.get("away") or {}).get("name", "")
    competition = match.get("competition", "")
    match_id = match.get("id", "")

    _fire(
        alarm, "soccer_drama_radar",
        lambda h, a, c, lang: t_soccer_body_drama_radar(h, a, c, score.total, lang),
        home_name, away_name, competition,
        signal=f"drama_{match_id}_{bucket}",
        one_shot=False,  # radar keeps watching other matches
    )


def _log_drama_snapshot(match_id: str | None, minute: int, score) -> None:
    if _db_engine is None or not match_id:
        return
    try:
        from sqlalchemy import text
        with _db_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO drama_snapshots "
                    "(match_id, sport, minute, state_score, momentum_score, "
                    " events_score, stakes_mult, drama_total) "
                    "VALUES (:m, 'football', :min, :st, :mo, :ev, :sk, :tot)"
                ),
                {
                    "m":   match_id,
                    "min": minute,
                    "st":  score.state,
                    "mo":  score.momentum,
                    "ev":  score.events,
                    "sk":  score.stakes,
                    "tot": score.total,
                },
            )
            conn.commit()
    except Exception as exc:
        logger.debug("drama snapshot log failed for %s: %s", match_id, exc)


def _comeback_sides(match: dict) -> set[str]:
    """Sides ('home'/'away') that trailed by >= _COMEBACK_DEFICIT at some point
    and are now within _COMEBACK_TARGET (level/ahead counts too). Reconstructed
    from the goal incidents' running scores, so it's stateless across ticks."""
    home = match.get("home") or {}
    away = match.get("away") or {}
    hs_now = home.get("score") or 0
    as_now = away.get("score") or 0
    # Seed with the current scoreline so a peak deficit is caught even if the
    # incident that produced it lacked a running score.
    max_home_deficit = as_now - hs_now
    max_away_deficit = hs_now - as_now
    goals = [inc for inc in (match.get("incidents") or [])
             if "goal" in (inc.get("type") or inc.get("incidentType") or "").lower()]
    for inc in sorted(goals, key=lambda i: i.get("minute") or i.get("time") or 0):
        h = inc.get("home_score")
        a = inc.get("away_score")
        if h is None or a is None:
            continue
        max_home_deficit = max(max_home_deficit, a - h)
        max_away_deficit = max(max_away_deficit, h - a)
    sides: set[str] = set()
    if max_home_deficit >= _COMEBACK_DEFICIT and (as_now - hs_now) <= _COMEBACK_TARGET:
        sides.add("home")
    if max_away_deficit >= _COMEBACK_DEFICIT and (hs_now - as_now) <= _COMEBACK_TARGET:
        sides.add("away")
    return sides


def _recent_xg15(match: dict) -> tuple[float, float]:
    """(home, away) expected goals over the last ~15 minutes. Prefers a
    precomputed xg_last_15 on the side dicts; otherwise derives it from the
    xg_per_minute timeline. Returns (0.0, 0.0) when no xG data is available."""
    home = match.get("home") or {}
    away = match.get("away") or {}
    h, a = home.get("xg_last_15"), away.get("xg_last_15")
    if h is not None and a is not None:
        return float(h or 0.0), float(a or 0.0)
    timeline = match.get("xg_per_minute") or []
    minute = match.get("minute") or 0
    if not timeline or minute <= 0:
        return 0.0, 0.0
    cum_start = drama_engine._cum_xg_at(timeline, max(0, minute - 15))
    cum_now = drama_engine._cum_xg_at(timeline, minute)
    if cum_start is None or cum_now is None:
        return 0.0, 0.0
    return max(0.0, cum_now[0] - cum_start[0]), max(0.0, cum_now[1] - cum_start[1])


def _recent_incident(match: dict, *, types: tuple[str, ...], window_min: int) -> bool:
    """True if an incident with one of `types` occurred within the last `window_min` minutes."""
    incidents = match.get("incidents") or []
    minute_now = match.get("minute") or 0
    cutoff = minute_now - window_min
    for inc in incidents:
        # Canonical shape: type=penalty, minute=N (older shapes used
        # incidentType=penalty, time=N — tolerate both).
        inc_type = (inc.get("type") or inc.get("incidentType") or "").lower()
        inc_min = inc.get("minute") or inc.get("time") or 0
        if any(t in inc_type for t in types) and inc_min >= cutoff:
            return True
    return False


def _recent_penalty_minute(match: dict) -> int | None:
    """Minute of the most recent penalty-award incident, or None. Used to dedup
    a match_moments penalty alert by the incident minute (stable across ticks),
    NOT by the live clock which advances every poll. Handles the canonical
    (type=penalty, minute=N) and older (incidentType=penalty, time=N) shapes."""
    incidents = match.get("incidents") or []
    minutes: list[int] = []
    for inc in incidents:
        inc_type = (inc.get("type") or inc.get("incidentType") or "").lower()
        inc_class = (inc.get("incidentClass") or "").lower()
        if "penalty" in inc_type or "penalty" in inc_class:
            minutes.append(inc.get("minute") or inc.get("time") or 0)
    if not minutes:
        return None
    return max(minutes)


def _find_recent_red_card(match: dict) -> dict | None:
    """Return the most recent red-card incident, or None."""
    incidents = match.get("incidents") or []
    candidates: list[dict] = []
    for inc in incidents:
        inc_type = (inc.get("type") or inc.get("incidentType") or "").lower()
        card_type = (inc.get("card_type") or inc.get("incidentClass") or "").lower()
        if "card" in inc_type and "red" in card_type:
            candidates.append({
                "minute": inc.get("minute") or inc.get("time") or 0,
                "player": inc.get("player") or inc.get("playerName") or "",
            })
        # Some sources type red cards as "red"/"redcard" directly.
        if inc_type == "red" or card_type == "redcard":
            candidates.append({
                "minute": inc.get("minute") or inc.get("time") or 0,
                "player": inc.get("player") or "",
            })
    if not candidates:
        return None
    return max(candidates, key=lambda x: x["minute"])


# ---------------------------------------------------------------------------
# Deactivation helper


def _deactivate_alarms(alarm_ids: list[int], *, reason: str) -> None:
    """Bulk-mark a list of soccer alarm rows as inactive."""
    if not alarm_ids or _db_engine is None or _alarm_model is None:
        return
    try:
        from sqlalchemy.orm import Session
        with Session(_db_engine) as session:
            session.query(_alarm_model).filter(
                _alarm_model.id.in_(alarm_ids),
                _alarm_model.is_active.is_(True),
            ).update({"is_active": False}, synchronize_session=False)
            session.commit()
        logger.info("soccer radar: deactivated %d alarm(s) — %s", len(alarm_ids), reason)
    except Exception as exc:
        logger.warning("soccer radar: deactivate failed (%s): %s", reason, exc)


# ---------------------------------------------------------------------------
# Firing


def _fire(
    alarm: dict,
    title_key: str,
    body_fn,
    home_name: str,
    away_name: str,
    competition: str,
    *,
    signal: str,
    one_shot: bool,
) -> None:
    """Send the notification + persist fired_at. Dedup via _fire_once_fn."""
    if _send_push is None or _fire_once_fn is None:
        logger.debug("soccer monitor not fully wired; would have fired alarm %s/%s", alarm["id"], signal)
        return

    user_token = alarm["user_token"]
    dedup_key = f"soccer_{alarm['id']}_{signal}"
    if not _fire_once_fn(dedup_key, user_token):
        return

    lang = "en"
    if _get_user_language is not None:
        try:
            lang = _get_user_language(user_token) or "en"
        except Exception:
            pass

    title = t_title(title_key, lang)
    body = body_fn(home_name, away_name, competition, lang)
    alert_type = alarm.get("alert_type") or "alarm"

    try:
        _send_push(user_token, title, body, alert_type=alert_type)
    except Exception as exc:
        logger.warning("soccer FCM send failed for alarm %s: %s", alarm["id"], exc)
        return

    if one_shot and _db_engine is not None and _alarm_model is not None:
        try:
            from sqlalchemy.orm import Session
            with Session(_db_engine) as session:
                row = session.get(_alarm_model, alarm["id"])
                if row is not None:
                    row.is_active = False
                    row.fired_at = datetime.now(timezone.utc)
                    session.commit()
        except Exception as exc:
            logger.warning("soccer alarm %s: failed to mark fired: %s", alarm["id"], exc)
