python3 manage.py shell <<'ORM'
from graphs.sync_avni_data import *
a = avni_sync()
slum_ids = [
    1988, 1996, 1987, 1991, 1973, 1974, 1990, 1992, 1984, 1998,
    1980, 1975, 1982, 1977, 1978, 1986, 1985, 1981, 1997, 1994,
    1989, 1999, 1995, 1976, 1983, 1979, 1993
]
for sid in slum_ids:
    print(f"🔹 Syncing Slum ID: {sid}")
    try:
        a.sync_rim_data(sid)
        print(f"✅ Completed: {sid}")
    except Exception as e:
        print(f"❌ Failed {sid}: {e}")
ORM
