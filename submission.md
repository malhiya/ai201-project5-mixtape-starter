# Mixtape — Codebase Map

Mixtape is a small Flask + SQLAlchemy web app where friends share songs, rate
them, build collaborative playlists, keep a daily listening streak, and see a
"friends listening now" feed. This is a map of how the code is put together.

## The big picture: three layers

Every request travels through the same three layers, top to bottom:

```
HTTP request
   │
   ▼
routes/      ← the "front door": read input, return JSON. No real logic here.
   │
   ▼
services/    ← the "brains": all the business rules + database reads/writes.
   │
   ▼
models.py    ← the database tables (what data looks like on disk).
```

`app.py` sits above all of it and wires everything together at startup.

---

## Main files and what each one does

### `app.py` — startup / wiring
The Flask **application factory**. `create_app()` does four things:
1. Creates the Flask app and sets config (the database is SQLite: `mixtape.db`).
2. Calls `db.init_app(app)` to connect SQLAlchemy.
3. Registers the four route groups ("blueprints") under URL prefixes:
   `/songs`, `/playlists`, `/users`, `/feed`.
4. Runs `db.create_all()` so the tables exist.

It also defines the shared `db` object that every other file imports.

### `models.py` — the database tables
Defines **7 tables** plus **3 join tables**. Every model has a `to_dict()`
method used to turn a database row into JSON.

Main tables:
- **User** — id, username, email, `listening_streak`, `last_listened_at`.
- **Song** — title, artist, album, genre, and `shared_by` (the user id of the
  person who shared it). This `shared_by` field is important — it's how the
  notification flow knows who to notify.
- **Tag** — a label that can be attached to songs.
- **ListeningEvent** — one row every time a user listens to a song
  (who + which song + when). The feed is built from these rows.
- **Rating** — a user's 1–5 score for a song. A `UniqueConstraint` means one
  user can only have one rating per song. So ratings live in their **own
  table**, not on the Song itself.
- **Playlist** — has a name, a creator (`created_by`), and an
  `is_collaborative` flag.
- **Notification** — a message for a user (`user_id`), with a `type`, a `body`
  string, and a `read` flag.

Join tables (many-to-many links):
- **friendships** — connects users to their friends (user_id ↔ friend_id).
- **song_tags** — connects songs to tags.
- **playlist_entries** — connects playlists to songs, and importantly stores a
  **`position`** column. So a song's place in a playlist is an explicit number,
  not just the order it was inserted. It also records `added_by` and `added_at`.

### `routes/` — the front door (4 files)
Each file is a Flask blueprint. The routes are thin: they pull values out of the
request, call one service function, and turn the result into JSON. Errors from
services (`ValueError`) get caught and returned as a 400/404 with a message.

- **routes/songs.py**
  - `GET /songs/search?q=...` → `search_songs()`
  - `GET /songs/<id>` → `get_song()`
  - `POST /songs/<id>/rate` → `rate_song()`
  - `POST /songs/<id>/listen` → `record_listening_event()`
- **routes/playlists.py**
  - `POST /playlists/` → `create_playlist()`
  - `GET /playlists/<id>` → `get_playlist()`
  - `GET /playlists/<id>/songs` → `get_playlist_songs()`
  - `POST /playlists/<id>/songs` → `add_to_playlist()`
- **routes/users.py**
  - `GET /users/<id>` → reads the User directly
  - `GET /users/<id>/streak` → `get_streak()`
  - `GET /users/<id>/notifications` → `get_notifications()`
  - `POST /users/notifications/<id>/read` → `mark_as_read()`
- **routes/feed.py**
  - `GET /feed/<id>/listening-now` → `get_friends_listening_now()`
  - `GET /feed/<id>/activity` → `get_activity_feed()`

### `services/` — the brains (5 files)
This is where the actual work and rules live.

- **services/search_service.py** — `search_songs(query)` finds songs whose title
  or artist contains the query (case-insensitive). `get_song(id)` fetches one.
- **services/notification_service.py** — the "someone interacted with your song"
  logic. Contains `create_notification()` (the shared helper), `add_to_playlist()`,
  `rate_song()`, `get_notifications()`, and `mark_as_read()`.
- **services/streak_service.py** — `record_listening_event()` writes a listen and
  then calls `update_listening_streak()`, which bumps or resets the user's daily
  streak based on when they last listened. `get_streak()` reads it back.
- **services/feed_service.py** — builds feeds by querying `ListeningEvent` rows
  from the current user's friends. `get_friends_listening_now()` shows only the
  last 24 hours (one most-recent song per friend); `get_activity_feed()` shows
  the most recent events with no time limit.
- **services/playlist_service.py** — `create_playlist()`, `get_playlist()`,
  `get_playlist_songs()` (returns songs ordered by their `position`), and
  `get_user_playlists()`.

---

## Data flow: adding a song to a playlist creates a notification

This is a full trace of one real feature — what happens when a user adds a
friend's song to a playlist, and how the original sharer gets notified.

**Step 1 — the request comes in.**
A `POST /playlists/<playlist_id>/songs` request arrives with a JSON body:
`{ "song_id": ..., "added_by": ... }`.

**Step 2 — the route handles it.**
`add_song()` in [routes/playlists.py](routes/playlists.py) reads `song_id` and
`added_by` from the body. If either is missing it returns a 400. Otherwise it
calls the service function `add_to_playlist(playlist_id, song_id, added_by)`.
The route itself contains **no logic** beyond parsing and error formatting.

**Step 3 — the service does the work.**
`add_to_playlist()` in
[services/notification_service.py](services/notification_service.py):
1. Looks up the `Song`, the adding `User`, and the `Playlist`. If any is
   missing, it raises `ValueError` (which the route turns into a 400).
2. If the song isn't already in the playlist, it appends it and commits —
   this writes a new row in the `playlist_entries` join table.
3. **The notification part:** it checks `song.shared_by`. If the person who
   added the song is *not* the person who originally shared it, it calls
   `create_notification(...)` for `song.shared_by` (the original sharer) with a
   message like *"Alice added your song 'X' to the playlist 'Y'."*

**Step 4 — the notification is saved.**
`create_notification()` builds a `Notification` row (recipient, type, body),
adds it to the session, and commits. It now lives in the database.

**Step 5 — the sharer sees it later.**
When the original sharer calls `GET /users/<id>/notifications`
([routes/users.py](routes/users.py)), that route calls `get_notifications()`,
which queries their `Notification` rows newest-first and returns them as JSON.

So the two users never call each other directly — the notification is passed
between them **through the database**. One user's action writes a row; the other
user's request reads it back later.

---

# Root Cause Analysis

## Issue #5: The last song in a playlist never shows up | `playlist_service.py`

### How bug was reproduced:

**Data condition:** The seeded database has a playlist called *"Late Night Vibes"*
that contains **7 songs** (rows in the `playlist_entries` table).

**Steps I took:**
1. Seeded the database and started the app:
   ```bash
   python seed_data.py
   FLASK_APP=app:create_app flask run
   ```
2. Checked how many songs the playlist *really* has, straight from the database:
   ```bash
   sqlite3 instance/mixtape.db "SELECT p.id, p.name, COUNT(pe.song_id) AS real_count FROM playlist p JOIN playlist_entries pe ON pe.playlist_id = p.id GROUP BY p.id;"
   ```
   This showed *Late Night Vibes* with a `real_count` of **7**.
3. Asked the endpoint for that same playlist's songs (using the playlist id from
   the previous step):
   ```bash
   curl -s "http://127.0.0.1:5000/playlists/<playlist_id>/songs" | python -m json.tool
   ```
   This endpoint doesn't contain the logic itself — the `GET
   /playlists/<id>/songs` route in [routes/playlists.py](routes/playlists.py)
   simply calls the service function `get_playlist_songs()` in
   [services/playlist_service.py](services/playlist_service.py) and wraps the
   result as `{"songs": ..., "count": len(songs)}`. So the `count` in the
   response is just the length of whatever that function returns — hitting the
   endpoint is exercising the real function, one layer removed.
4. To confirm the bug is in the service function and not the route wrapper, I
   also called `get_playlist_songs()` directly against the seeded database (no
   server needed):
   ```bash
   python -c "from app import create_app, db; from models import Playlist; from services.playlist_service import get_playlist_songs; app=create_app();
   app.app_context().push();
   pid=db.session.query(Playlist).filter_by(name='Late Night Vibes').first().id;
   print('returned:', len(get_playlist_songs(pid)))"
   ```
   This printed `returned: 6`, matching the endpoint — confirming the function
   itself is dropping a song, independent of the HTTP layer.

**What happened (the bug):** The endpoint returned `"count": 6` — only **6**
songs, even though the playlist has **7**. The song in the last position is
always missing.

**Trigger:** Any playlist with one or more songs. The last song (highest
`position`) is always dropped, so the returned count is always one less than the
real count.

### How root cause was found:

The README identified `playlist_service.py` as the relevant file. I traced the
request from `routes/playlists.py`, where `GET /playlists/<id>/songs` delegates
directly to `get_playlist_songs()`, into that function. Reading it top to bottom,
the query and ordering were clearly correct, which left the return statement as
the only remaining suspect — and its list slice confirmed the cause.

### Root cause:

The function ends with `return [song.to_dict() for song in songs[:-1]]`. The
`[:-1]` slice excludes the final element of the list, so the song with the
highest `position` is discarded before the response is built. The data and the
query are correct; the last song is dropped solely by this slice in the return
statement.

### Fix and Side-Effect Check:

I changed the return statement from
`return [song.to_dict() for song in songs[:-1]]` to
`return [song.to_dict() for song in songs]`. Removing the `[:-1]` slice means the
function now iterates over every song the query returned, including the one at the
highest `position`. This addresses the root cause directly, since the slice was
the only place a song was being dropped.

Afterward I checked the other code that touches the same feature. The
`GET /playlists/<id>/songs` route in `routes/playlists.py` only reports
`len(songs)`, so it needs no change and now returns the correct count.
`add_to_playlist()` in `notification_service.py` imports `get_playlist_songs` but
never calls it, so the add-and-notify flow is unaffected. Finally I ran
`tests/test_playlists.py`, which covers an empty playlist, a multi-song playlist,
and song ordering; all three pass, confirming the fix works on both sides of the
boundary without breaking the empty-playlist case.

---

## Issue #4: No notification when a friend rates my song

### How bug was reproduced

**Data condition:** The song *"Crown Heights Anthem"* is shared by the user
**simone**, and simone starts with **0** notifications. **nova** is a different
user who will rate the song.

**Steps I took:**
1. Seeded the database and started the app:
   ```bash
   python seed_data.py
   FLASK_APP=app:create_app flask run
   ```
2. Looked up the song id and the two user ids from the database:
   ```bash
   sqlite3 instance/mixtape.db "SELECT s.id, s.title FROM song s JOIN user u ON u.id=s.shared_by WHERE u.username='simone' LIMIT 1;"
   sqlite3 instance/mixtape.db "SELECT username, id FROM user WHERE username IN ('simone','nova');"
   ```
3. Checked simone's notifications **before** rating:
   ```bash
   curl -s "http://127.0.0.1:5000/users/<simone_id>/notifications" | python -m json.tool
   ```
   Result: `"count": 0`.
4. Had nova rate simone's song:
   ```bash
   curl -s -X POST "http://127.0.0.1:5000/songs/<song_id>/rate" \
     -H "Content-Type: application/json" \
     -d '{"user_id":"<nova_id>","score":5}' | python -m json.tool
   ```
   Result: the rating saved successfully (`"score": 5` came back).
5. Checked simone's notifications **after** rating (same command as step 3).

**What happened (the bug):** Even though the rating saved, simone's notification
count was still `"count": 0` afterward. She was never told her song was rated.

**Trigger:** Any user rating another user's song. Rating saves the score but
never creates a notification for the song's sharer — unlike adding a song to a playlist, which does notify the sharer.


### How the root cause was found:

The README identified `notification_service.py` as the relevant file. Since
adding a song to a playlist *does* notify the sharer, I compared the two
functions in that file side by side. `add_to_playlist()` looks up the song,
performs its write, and then calls `create_notification()` for `song.shared_by`.
Reading `rate_song()` the same way, it looks up the song and user, saves or
updates the `Rating`, commits, and returns — with no `create_notification()`
call anywhere. The notification step present in every other interaction was
simply missing from this one.

### Root Cause:

`rate_song()` saved the rating but never created a notification. After
committing the `Rating`, the function returned immediately, so the song's
sharer was never informed. Unlike `add_to_playlist()`, it had no
`create_notification()` call for `song.shared_by`, which is why rating another
user's song produced no notification even though the score was recorded
correctly.

### Fix and Side-Effect Check:

I added the missing notification step to `rate_song()`, mirroring the pattern
already used by `add_to_playlist()`: after the rating is committed, if the rater
is not the song's sharer (`song.shared_by != user_id`), the function calls
`create_notification()` for `song.shared_by` with a `"song_rated"` type and a
message like *"nova rated your song 'Crown Heights Anthem' 5 out of 5."* The
self-check prevents users from being notified about rating their own songs, the
same guard `add_to_playlist()` uses.

Afterward I checked the code that touches the same feature. The
`POST /songs/<id>/rate` route in `routes/songs.py` is the only caller; it just
returns `rating.to_dict()`, and since the notification is created after the
rating is committed and the same `rating` object is still returned, the route's
response is unchanged. `create_notification()` performs its own commit, but the
rating was already persisted, so nothing is detached or lost. The
`UniqueConstraint` on `Rating` is untouched — re-rating still updates the
existing row rather than inserting a duplicate, and simply fires a fresh
notification. I confirmed the behavior end to end: after nova rated simone's
song, simone's notification count went from `0` to `1` with the expected
`song_rated` message, and rating one's own song still produces no notification.

--- 

## Issue #1: My listening streak keeps resetting

### How bug was reproduced

**Data condition:** A single user with no prior listening history, so their
streak starts fresh at `1`. The bug depends on *when* they listen, not on any
seeded data — specifically on whether a listen lands on a **Sunday**.
`update_listening_streak(user, now)` takes the clock as an argument, so no server
or database seeding is needed; the dates can be injected directly.

**Steps I took:**
1. Wrote a small script (`repro_streak.py`) that runs against an in-memory
   database and demonstrates the bug three ways:
   ```bash
    python repro_streak.py
   ```
2. **Demonstration 1 — full-week sweep of consecutive-day pairs.** For every
   pair (Mon→Tue, Tue→Wed, … Sun→Mon) it uses a fresh user and checks whether the
   streak increments or resets. This proves *which* day, if any, misbehaves
   rather than testing Sunday alone:
   ```
   Mon -> Tue: streak=2  incremented (ok)
   Tue -> Wed: streak=2  incremented (ok)
   Wed -> Thu: streak=2  incremented (ok)
   Thu -> Fri: streak=2  incremented (ok)
   Fri -> Sat: streak=2  incremented (ok)
   Sat -> Sun: streak=1  reset  <-- BUG
   Sun -> Mon: streak=2  incremented (ok)
   ```
3. **Demonstration 2 — a real streak built up Mon..Sun.** Listening every day
   builds the streak normally, then it collapses the moment the user listens on
   Sunday — proving the streak's *length* is irrelevant:
   ```
   Mon 2024-06-10: streak=1
   Tue 2024-06-11: streak=2
   Wed 2024-06-12: streak=3
   Thu 2024-06-13: streak=4
   Fri 2024-06-14: streak=5
   Sat 2024-06-15: streak=6
   Sun 2024-06-16: streak=1  <-- BUG: collapsed
   ```
4. **Demonstration 3 — control case, a streak built up Sun..Sat.** Starting on a
   Sunday is fine (day 1 always starts at 1) and the rest of the week builds
   normally, confirming the reset only ever fires when *today* is a Sunday — never
   based on what day the streak started:
   ```
   Sun 2024-06-16: streak=1
   Mon 2024-06-17: streak=2
   Tue 2024-06-18: streak=3
   Wed 2024-06-19: streak=4
   Thu 2024-06-20: streak=5
   Fri 2024-06-21: streak=6
   Sat 2024-06-22: streak=7
   ```

**What happened (the bug):** A user's listening streak resets to `1` whenever
they listen on a Sunday, even though they listened the day before (Saturday) and
the streak should simply continue. A six-day streak drops straight back to `1`.

**Trigger:** Any streak that continues onto a Sunday — regardless of how many days
it had accumulated — resets to `1`. In `update_listening_streak()` the increment
branch is guarded by `days_since_last == 1 and today.weekday() != 6`; because
Sunday is `weekday() == 6`, that condition is false and the code falls through to
the `else` branch that resets the streak to `1`.


### How the root cause was found:

The full-week sweep (Demonstration 1) isolated the failure to a single transition:
every consecutive-day pair incremented *except* `Sat -> Sun`, which reset to `1`.
The control case (Demonstration 3) showed that starting a streak on Sunday was
fine — only *ending* a day's listen on a Sunday broke. That pointed the
investigation at day-of-week handling in the increment path rather than at the
date-difference math or the streak counter itself. Reading
`update_listening_streak()` in `services/streak_service.py`, the increment branch
was guarded by `days_since_last == 1 and today.weekday() != 6`. Since
`weekday() == 6` is Sunday, the guard was the only piece of logic that singled
out one weekday — matching exactly the one day the sweep flagged.

### Root Cause:

In `update_listening_streak()`, the consecutive-day increment branch carried a
spurious extra condition:

```python
elif days_since_last == 1 and today.weekday() != 6:
    user.listening_streak += 1
```

The `and today.weekday() != 6` clause has no basis in the documented streak rules
("if the user listened yesterday, the streak increments by 1"). When today is a
Sunday (`weekday() == 6`) the whole condition evaluates to `False` even for a
genuine consecutive-day listen, so control falls through to the `else` branch,
which resets `listening_streak` to `1`. The result: any streak that continues
onto a Sunday collapses back to `1`.

### Fix and Side-Effect Check:

**Fix** — removed only the extraneous weekday clause so the branch matches the
documented rule (`services/streak_service.py`):

```python
elif days_since_last == 1:
    user.listening_streak += 1
```

This is the minimal change: one condition removed, no surrounding logic touched.

**Side-effect check** — I traced every place that reads or writes the same data
and feature:
- `tests/test_streaks.py` — full suite passes; `test_streak_increments_on_sunday`
  (Sat→Sun) now passes, and the existing first-listen / consecutive /
  same-day / skipped-day cases are unaffected (they don't involve Sunday).
- `routes/songs.py` → `record_listening_event()` → `update_listening_streak()` —
  the live path; behavior changes only on Sundays, and only in the correct
  direction.
- `routes/users.py` → `get_streak()` — read-only, returns the stored value;
  unaffected.
- `services/feed_service.py` — reads `ListeningEvent` rows for the feed; never
  touches `listening_streak`; unaffected.
- `seed_data.py` / `models.py` — set streak values directly, independent of the
  increment logic.

**Boundary verification (both sides of the Sunday boundary):**
- *Into Sunday* (Sat → Sun): now increments — the case that was broken.
- *Out of Sunday* (Sun → Mon): still increments — no regression on the side that
  already worked.
- A continuous Mon..Sun run now climbs `1..7` with no collapse at the Sunday
  index; same-day and skipped-day behavior on a Sunday still correctly hold
  (no change / reset to `1`).

The `repro_streak.py` script was updated to flag a collapse based on the *actual*
streak value rather than assuming Sunday, so it now serves as a regression check:
with the fix in place every row reads clean.



















