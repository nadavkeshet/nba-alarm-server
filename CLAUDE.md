# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language

Always respond in English, regardless of what language the user writes in.

## Project Overview

Clutch Alarm is an NBA game alarm app. Users set alarms for game score conditions (e.g., close game within X points) or player stat milestones, and receive loud push notifications that bypass silent mode. It consists of a React Native/Expo mobile app and a Python FastAPI backend.

## Architecture

**Monorepo with two main components:**

- **`mobile/Android/`** — Expo/React Native app (TypeScript). File-based routing via Expo Router with bottom-tab navigation (`app/(tabs)/`). Uses React Context for state (theme, language), AsyncStorage for persistence.
- **`Server/`** — FastAPI backend (Python). Single `main.py` for API endpoints + SQLAlchemy models, `monitor.py` for background monitoring threads, `epg.py` for broadcast info, `i18n.py` for notification translations.

**Notification flow:** Server polls ESPN APIs via background threads -> dedup check (`_fire_once`) -> sends data-only FCM message -> mobile app receives via Firebase Messaging -> displays via Notifee on STREAM_ALARM channel (bypasses silent mode) -> custom Expo plugins enable full-screen intent and screen wake.

**Three monitoring modes:**
1. Manual game alarm — one thread per alarm, watches score differential
2. Manual player alarm — one thread per alarm, watches player stats
3. Radar — single continuous thread, checks all users' favorites against all live games

**Database (PostgreSQL):** `alarms` (one-shot), `favorites` (persistent players), `favorite_teams` (persistent teams), `user_preferences` (radar config), `fired_alerts` (dedup log). Server recovers alarm threads and fired alerts from DB on restart.

## Common Commands

### Mobile (from `mobile/Android/`)
```bash
npm start              # Start Expo dev server
expo run:android       # Build & run on Android
expo run:ios           # Build & run on iOS
expo lint              # ESLint
npm install            # Install dependencies
```

### Server (from `Server/`)
```bash
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8001   # Local dev
pip install -r requirements.txt                                     # Install deps
```

Production uses `Procfile`: `uvicorn main:app --host 0.0.0.0 --port $PORT`

## Key Implementation Details

- **`mobile/Android/config.ts`** controls server URL — toggle between `LOCAL_IP` and `PROD_URL` for dev/prod
- **Expo config plugins** (`mobile/Android/plugins/`) modify native Android config for alarm audio attributes, full-screen intent, screen wake, and sound file bundling
- **`mobile/Android/firebaseMessaging.js`** handles FCM in background/killed states; `alarmSoundHandler.ts` controls native alarm audio
- **i18n:** English + Hebrew. Mobile uses `i18n/en.ts` and `i18n/he.ts` via LanguageContext. Server uses `i18n.py` for notification text
- **FCM token migration:** `/register-token` accepts old token and migrates all associated data to the new token
- **Dedup:** In-memory set + DB table, daily cleanup on server restart
- **Server auto-deploys on Railway** from https://github.com/nadavkeshet/nba-alarm-server — pushes to that repo trigger deployment. Mobile builds via EAS (Expo Application Services)
