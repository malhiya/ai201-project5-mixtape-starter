"""
repro_streak.py — reproduce the "streak keeps resetting" bug.

Runs every consecutive-day pair across a full week with a fresh user each time
and prints whether the streak incremented or reset. Only the pair ending on
Sunday should break, isolating the bug to weekday() == 6.
"""

from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User
from services.streak_service import update_listening_streak

app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
with app.app_context():
    db.create_all()
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    # Jun 10 2024 is a Monday; test each day paired with the day after it.
    for offset in range(7):
        day1 = datetime(2024, 6, 10, 12, 0, tzinfo=timezone.utc) + timedelta(days=offset)
        day2 = day1 + timedelta(days=1)
        u = User(username=f"u{offset}", email=f"u{offset}@x.app")
        db.session.add(u)
        db.session.commit()
        update_listening_streak(u, day1)   # streak -> 1
        update_listening_streak(u, day2)   # consecutive day
        verdict = "reset  <-- BUG" if u.listening_streak == 1 else "incremented (ok)"
        print(f"{names[day1.weekday()]} -> {names[day2.weekday()]}: "
              f"streak={u.listening_streak}  {verdict}")

    # --- Demonstration 2: a continuous streak built up Mon..Sun ---
    # Shows that it does NOT matter how long the streak is: any streak that
    # reaches Sunday collapses back to 1, even a 6-day run.
    print("\nContinuous streak, listening every day Mon..Sun:")
    u = User(username="continuous", email="c@x.app")
    db.session.add(u)
    db.session.commit()
    for offset in range(7):
        day = datetime(2024, 6, 10, 12, 0, tzinfo=timezone.utc) + timedelta(days=offset)
        update_listening_streak(u, day)
        flag = "  <-- BUG: collapsed" if names[day.weekday()] == "Sun" else ""
        print(f"  {names[day.weekday()]} 2024-06-{10 + offset}: streak={u.listening_streak}{flag}")

    # --- Demonstration 3: a continuous streak built up Sun..Sat ---
    # Starting ON a Sunday is fine (day 1 always starts at 1), and the rest of
    # the week builds normally. The reset only ever fires when *today* is Sunday.
    print("\nContinuous streak, listening every day Sun..Sat:")
    u = User(username="continuous2", email="c2@x.app")
    db.session.add(u)
    db.session.commit()
    for offset in range(7):
        day = datetime(2024, 6, 16, 12, 0, tzinfo=timezone.utc) + timedelta(days=offset)
        update_listening_streak(u, day)
        flag = "  <-- BUG: collapsed" if names[day.weekday()] == "Sun" else ""
        print(f"  {names[day.weekday()]} 2024-06-{16 + offset}: streak={u.listening_streak}{flag}")
