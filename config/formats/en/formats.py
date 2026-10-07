"""English date formats for the app.

Django's own `en` formats are American (`Oct. 20, 2026`, `10/20/2026`,
`4 p.m.`). The game runs on Berlin time for European players, so the English
screens read day-first with a 24-hour clock -- only the words are English.
"""

DATE_FORMAT = "j F Y"
SHORT_DATE_FORMAT = "j M Y"
TIME_FORMAT = "H:i"
DATETIME_FORMAT = "j F Y, H:i"
SHORT_DATETIME_FORMAT = "j M Y, H:i"
MONTH_DAY_FORMAT = "j F"
YEAR_MONTH_FORMAT = "F Y"
