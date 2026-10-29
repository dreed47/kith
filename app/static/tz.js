// Tell the server this browser's time zone, so times and "today" are shown in it (app/localtime.py).
document.cookie = `tz=${Intl.DateTimeFormat().resolvedOptions().timeZone};path=/;max-age=31536000;samesite=lax`;
